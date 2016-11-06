# InputArgs
A sublime text plugin to send custom input arguments when building using 'ctrl + b'. View a program output with custom arguments from within sublime text (see http://stackoverflow.com/questions/16490889/build-and-run-with-arguments-in-sublime-text-2 for discussion and alternatives).

##Installing

###OSX

```
$ cd ~/Library/Application\ Support/Sublime\ Text\ <2/3>/Packages/
$ git clone git://github.com/bilalba/InputArgs.git InputArgs

```

###Linux (Ubuntu like distros)

```
$ cd ~/.config/sublime-text-<2/3>/Packages/
$ git clone git://github.com/bilalba/InputArgs.git InputArgs

```

###Windows

```
Copy the directory to: "C:\Users\<username>\AppData\Roaming\Sublime Text <2/3>\Packages"

```


## Usage

Standard sublime text build system commands.

Use "ctrl+b" to run the build system and an input dialog will ask for arguments.

New build systems can be added through "Tools>Build System>New Build System".

If you'd rather not override your build system and instead would prefer to customize it, you can use the `target` option and change the name of the plugin command from `ExecCommand`; e.g., `InputArgsCommand` would match the target option `input_args` as shown in the below example:

### Example build system
```
{
  "shell_cmd" : ["make $file_base_name && ./$file_base_name"],
  "selector" : "source.c",
  "shell": true,
  "target": "input_args",
  "working_dir" : "$file_path",
}
```