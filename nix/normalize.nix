# PEP 503 name normalisation: lower-case, runs of "-", "_" and "." become one "-".
lib: name:
lib.toLower (lib.concatStringsSep "-" (lib.filter lib.isString (builtins.split "[-_.]+" name)))
