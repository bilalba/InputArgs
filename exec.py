import os, sys, re
import sublime, sublime_plugin
import html
import threading
import subprocess
import functools
import time
import collections

try:
    # python 3 / Sublime Text 3
    from .hist import history1 as history
except ValueError:
    from hist import history1 as history

class ProcessListener(object):
    def on_data(self, proc, data):
        pass

    def on_finished(self, proc):
        pass

# Encapsulates subprocess.Popen, forwarding stdout to a supplied
# ProcessListener (on a separate thread)
class AsyncProcess(object):
    def __init__(self, cmd, shell_cmd, env, listener,
            # "path" is an opt`ion in build systems
            path="",
            # "shell" is an options in build systems
            shell=False, *args, **kwargs):

        if not shell_cmd and not cmd:
            raise ValueError("shell_cmd or cmd is required")

        if shell_cmd and not isinstance(shell_cmd, str):
            raise ValueError("shell_cmd must be a string")

        self.listener = listener
        self.killed = False

        self.start_time = time.time()

        # Hide the console window on Windows
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        # Set temporary PATH to locate executable in cmd
        if path:
            old_path = os.environ["PATH"]
            # The user decides in the build system whether he wants to append $PATH
            # or tuck it at the front: "$PATH;C:\\new\\path", "C:\\new\\path;$PATH"
            os.environ["PATH"] = os.path.expandvars(path)

        proc_env = os.environ.copy()
        proc_env.update(env)
        for k, v in proc_env.items():
            proc_env[k] = os.path.expandvars(v)

        if shell_cmd and sys.platform == "win32":
            # Use shell=True on Windows, so shell_cmd is passed through with the correct escaping
            self.proc = subprocess.Popen(shell_cmd, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, startupinfo=startupinfo, env=proc_env, shell=True)
        elif shell_cmd and sys.platform == "darwin":
            # Use a login shell on OSX, otherwise the users expected env vars won't be setup
            self.proc = subprocess.Popen(["/bin/bash", "-l", "-c", shell_cmd], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, startupinfo=startupinfo, env=proc_env, shell=False)
        elif shell_cmd and sys.platform == "linux":
            # Explicitly use /bin/bash on Linux, to keep Linux and OSX as
            # similar as possible. A login shell is explicitly not used for
            # linux, as it's not required
            self.proc = subprocess.Popen(["/bin/bash", "-c", shell_cmd], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, startupinfo=startupinfo, env=proc_env, shell=False)
        else:
            # Old style build system, just do what it asks
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, startupinfo=startupinfo, env=proc_env, shell=shell)

        if path:
            os.environ["PATH"] = old_path

        if self.proc.stdout:
            threading.Thread(target=self.read_stdout).start()

        if self.proc.stderr:
            threading.Thread(target=self.read_stderr).start()

    def kill(self):
        if not self.killed:
            self.killed = True
            if sys.platform == "win32":
                # terminate would not kill process opened by the shell cmd.exe, it will only kill
                # cmd.exe leaving the child running
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                subprocess.Popen("taskkill /PID " + str(self.proc.pid), startupinfo=startupinfo)
            else:
                self.proc.terminate()
            self.listener = None

    def poll(self):
        return self.proc.poll() == None

    def exit_code(self):
        return self.proc.poll()

    def read_stdout(self):
        while True:
            data = os.read(self.proc.stdout.fileno(), 2**15)

            if len(data) > 0:
                if self.listener:
                    self.listener.on_data(self, data)
            else:
                self.proc.stdout.close()
                if self.listener:
                    self.listener.on_finished(self)
                break

    def read_stderr(self):
        while True:
            data = os.read(self.proc.stderr.fileno(), 2**15)

            if len(data) > 0:
                if self.listener:
                    self.listener.on_data(self, data)
            else:
                self.proc.stderr.close()
                break

class ExecCommand(sublime_plugin.WindowCommand, ProcessListener):
    BLOCK_SIZE = 2**14
    text_queue = collections.deque()
    text_queue_proc = None
    text_queue_lock = threading.Lock()

    proc = None

    errs_by_file = {}
    phantom_sets_by_buffer = {}
    show_errors_inline = True

    def run(self, cmd = None, shell_cmd = None, file_regex = "", line_regex = "", working_dir = "",
            encoding = "utf-8", env = {}, quiet = False, kill = False,
            update_phantoms_only=False, hide_phantoms_only=False,
            word_wrap = True, syntax = "Packages/Text/Plain text.tmLanguage",
            # Catches "path" and "shell"
            **kwargs):
        if update_phantoms_only:
            if self.show_errors_inline:
                self.update_phantoms()
            return
        if hide_phantoms_only:
            self.hide_phantoms()
            return
        self.sl = kwargs
        # clear the text_queue
        self.text_queue_lock.acquire()
        self.truth = False
        try:
            self.text_queue.clear()
            self.text_queue_proc = None
        finally:
            self.text_queue_lock.release()

        if kill:
            if self.proc:
                self.proc.kill()
                self.proc = None
                self.append_string(None, "[Cancelled]")
            return

        if not hasattr(self, 'output_view'):
            # Try not to call get_output_panel until the regexes are assigned
            if int(sublime.version()) >= 3000:
                self.output_view = self.window.create_output_panel("exec")
            else:
                self.output_view = self.window.get_output_panel("exec")

        # Default the to the current files directory if no working directory was given
        if (working_dir == "" and self.window.active_view()
                        and self.window.active_view().file_name()):
            working_dir = os.path.dirname(self.window.active_view().file_name())

        self.output_view.settings().set("result_file_regex", file_regex)
        self.output_view.settings().set("result_line_regex", line_regex)
        self.output_view.settings().set("result_base_dir", working_dir)
        self.output_view.settings().set("word_wrap", word_wrap)
        self.output_view.settings().set("line_numbers", False)
        self.output_view.settings().set("gutter", False)
        self.output_view.settings().set("scroll_past_end", False)
        if int(sublime.version()) >= 3000:
            self.output_view.assign_syntax(syntax)
        merged_env = env.copy()
        self.encoding = encoding
        self.quiet = quiet

        # Call create_output_panel a second time after assigning the above
        # settings, so that it'll be picked up as a result buffer

        # Change to the working dir, rather than spawning the process with it,
        # so that emitted working dir relative path names make sense
        if working_dir != "":
            os.chdir(working_dir)

        v = self.window.show_input_panel("Input Args", "", functools.partial(self.fun, cmd, shell_cmd, merged_env), None, None)
        v.settings().set('InputArgsInputPanel', True)


    def fun(self, cmd, shell_cmd, merged_env, ss):
        if int(sublime.version()) >= 3000:
            self.window.create_output_panel("exec")
        else:
            self.window.get_output_panel("exec")
        # self.window.create_output_panel("exec")
        # print("THIis" + str(ss))
        history.insert(ss)
        for s in str(ss).split()
            if shell_cmd:
                shell_cmd += " " + s
            elif str(ss):
                cmd.append(s)


        self.debug_text = ""
        if shell_cmd:
            self.debug_text += "[shell_cmd: " + shell_cmd + "]\n"
        else:
            self.debug_text += "[cmd: " + str(cmd) + "]\n"
        self.debug_text += "[dir: " + str(os.getcwd()) + "]\n"
        if "PATH" in merged_env:
            self.debug_text += "[path: " + str(merged_env["PATH"]) + "]"
        else:
            self.debug_text += "[path: " + str(os.environ["PATH"]) + "]"



        self.proc = None
        # print("BILLZ")
        # print(cmd)
        if not self.quiet:
            if shell_cmd:
                print("Running " + shell_cmd)
            elif cmd:
                print("Running " + " ".join(cmd))
            sublime.status_message("Building")
        show_panel_on_build = sublime.load_settings("Preferences.sublime-settings").get("show_panel_on_build", True)
        if show_panel_on_build:
            self.window.run_command("show_panel", {"panel": "output.exec"})


        if self.window.active_view():
            user_env = self.window.active_view().settings().get('build_env')
            if user_env:
                merged_env.update(user_env)

        try:
            # Forward kwargs to AsyncProcess

            self.proc = AsyncProcess(cmd, shell_cmd, merged_env, self, **self.sl)

            self.text_queue_lock.acquire()
            try:
                self.text_queue_proc = self.proc
            finally:
                self.text_queue_lock.release()

        except Exception as e:
            self.append_string(None, str(e) + "\n")
            self.append_string(None, self.debug_text + "\n")
            if not self.quiet:
                self.append_string(None, "[Finished]")


    def is_enabled(self, kill = False):
        if kill:
            return (self.proc != None) and self.proc.poll()
        else:
            return True

    def append_string(self, proc, str):
        self.text_queue_lock.acquire()

        was_empty = False
        try:
            if proc != self.text_queue_proc:
                # a second call to exec has been made before the first one
                # finished, ignore it instead of intermingling the output.
                if proc:
                    proc.kill()
                return

            if len(self.text_queue) == 0:
                was_empty = True
                self.text_queue.append("")

            available = self.BLOCK_SIZE - len(self.text_queue[-1])

            if len(str) < available:
                cur = self.text_queue.pop()
                self.text_queue.append(cur + str)
            else:
                self.text_queue.append(str)

        finally:
            self.text_queue_lock.release()

        if was_empty:
            sublime.set_timeout(self.service_text_queue, 0)

    def service_text_queue(self):
        self.text_queue_lock.acquire()

        is_empty = False
        try:
            if len(self.text_queue) == 0:
                # this can happen if a new build was started, which will clear
                # the text_queue
                return

            characters = self.text_queue.popleft()
            is_empty = (len(self.text_queue) == 0)
        finally:
            self.text_queue_lock.release()

        if int(sublime.version()) >= 3000:
            self.output_view.run_command('append', {'characters': characters, 'force': True, 'scroll_to_end': True})
            if self.show_errors_inline and characters.find('\n') >= 0:
                errs = self.output_view.find_all_results_with_text()
                errs_by_file = {}
                for file, line, column, text in errs:
                    if file not in errs_by_file:
                        errs_by_file[file] = []
                    errs_by_file[file].append((line, column, text))
                self.errs_by_file = errs_by_file

                self.update_phantoms()
        else:
            selection_was_at_end = (len(self.output_view.sel()) == 1 and self.output_view.sel()[0] == sublime.Region(self.output_view.size()))
            self.output_view.set_read_only(False)
            edit = self.output_view.begin_edit()
            self.output_view.insert(edit, self.output_view.size(), characters)
            if selection_was_at_end:
                self.output_view.show(self.output_view.size())
            self.output_view.end_edit(edit)
            self.output_view.set_read_only(True)

        if not is_empty:
            sublime.set_timeout(self.service_text_queue, 1)

    def finish(self, proc):
        if not self.quiet:
            elapsed = time.time() - proc.start_time
            exit_code = proc.exit_code()
            if exit_code == 0 or exit_code == None:
                # print("kutta scene")
                self.append_string(proc,
                    ("[Finished in %.1fs]" % (elapsed)))
            else:
                self.append_string(proc, ("[Finished in %.1fs with exit code %d]\n"
                    % (elapsed, exit_code)))
                self.append_string(proc, self.debug_text)

        if proc != self.proc:
            return

        errs = self.output_view.find_all_results()
        if len(errs) == 0:
            sublime.status_message("Build finished")
        else:
            sublime.status_message(("Build finished with %d errors") % len(errs))

    def on_data(self, proc, data):
        try:
            characters = data.decode(self.encoding)
        except:
            characters = "[Decode error - output not " + self.encoding + "]\n"
            proc = None

        # Normalize newlines, Sublime Text always uses a single \n separator
        # in memory.
        characters = characters.replace('\r\n', '\n').replace('\r', '\n')
        print(characters)
        self.append_string(proc, characters)

    def on_finished(self, proc):
        sublime.set_timeout(functools.partial(self.finish, proc), 0)

    def update_phantoms(self):
        stylesheet = '''
            <style>
                div.error {
                    padding: 0.4rem 0 0.4rem 0.7rem;
                    margin: 0.2rem 0;
                    border-radius: 2px;
                }

                div.error span.message {
                    padding-right: 0.7rem;
                }

                div.error a {
                    text-decoration: inherit;
                    padding: 0.35rem 0.7rem 0.45rem 0.8rem;
                    position: relative;
                    bottom: 0.05rem;
                    border-radius: 0 2px 2px 0;
                    font-weight: bold;
                }
                html.dark div.error a {
                    background-color: #00000018;
                }
                html.light div.error a {
                    background-color: #ffffff18;
                }
            </style>
        '''

        for file, errs in self.errs_by_file.items():
            view = self.window.find_open_file(file)
            if view:

                buffer_id = view.buffer_id()
                if buffer_id not in self.phantom_sets_by_buffer:
                    phantom_set = sublime.PhantomSet(view, "exec")
                    self.phantom_sets_by_buffer[buffer_id] = phantom_set
                else:
                    phantom_set = self.phantom_sets_by_buffer[buffer_id]

                phantoms = []

                for line, column, text in errs:
                    pt = view.text_point(line - 1, column - 1)
                    phantoms.append(sublime.Phantom(
                        sublime.Region(pt, view.line(pt).b),
                        ('<body id=inline-error>' + stylesheet +
                            '<div class="error">' +
                            '<span class="message">' + html.escape(text, quote=False) + '</span>' +
                            '<a href=hide>' + chr(0x00D7) + '</a></div>' +
                            '</body>'),
                        sublime.LAYOUT_BELOW,
                        on_navigate=self.on_phantom_navigate))

                phantom_set.update(phantoms)

    def hide_phantoms(self):
        for file, errs in self.errs_by_file.items():
            view = self.window.find_open_file(file)
            if view:
                view.erase_phantoms("exec")

        self.errs_by_file = {}
        self.phantom_sets_by_buffer = {}
        self.show_errors_inline = False

    def on_phantom_navigate(self, url):
        self.hide_phantoms()
