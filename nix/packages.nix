# System tools every Mojo shell needs; shared by mkDevShell and mojox's own shell.
pkgs:
with pkgs; [
  uv
  rustc
  cargo
  # Wrapped: `-fuse-ld=lld` runs the first ld.lld on PATH, and only the wrapper
  # records the RUNPATH of the store libraries a cdylib links against.
  (wrapBintoolsWith { bintools = lld; })
  gcc
  pkg-config
  cmake
  git
  cacert
  iproute2
  unzip
  zlib.dev
  brotli.dev
  zlib
  brotli.lib
]
