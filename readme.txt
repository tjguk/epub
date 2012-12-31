The idea is that you run epub as an admin tool. You start by
generating a named skeleton:

  epub.py startup <name> <author> [<uid>]

You then drop valid XHTML files into <name>/original-html and call

  ../epub.py build

from the <name> folder (this will be generated into the build.cmd script
as part of the template generation).

NB The epub xhtml command can be used to transform existing HTML
files into valid XHTML.

