#!/bin/sh
# util/tune_sweep.sh — build musl rv32 under each -mtune scheduling model.
#
# ISA is held constant; only the tuning model varies, so any difference in the
# output is the tuning model's doing. See results/corpus/TUNE.md for what came
# out of it. Expects musl-1.2.5 unpacked alongside $OUT and clang 18 with an
# lld that knows riscv32.
#
#   OUT=/tmp/corp sh util/tune_sweep.sh
#
# Only lib/libc.so is built: the static lib needs a riscv32-ar we do not have,
# and the shared object is what the disassembly corpus is made from.
set -e
OUT=${OUT:-/tmp/corp}
ARCH="-march=rv32gc_zba_zbb_zbs_zicond -mabi=ilp32d"
# nomisched is not a CPU: it is the generic model with scheduling switched off,
# the control for "how much is the compiler's scheduler doing to us".
for t in generic-rv32 rocket-rv32 sifive-7-series syntacore-scr1-max nomisched; do
  d=$OUT/t-$t
  case $t in
    nomisched) EXTRA="-mllvm -enable-misched=false -mllvm -enable-post-misched=false" ;;
    *)         EXTRA="-mtune=$t" ;;
  esac
  rm -rf "$d" && mkdir -p "$d" && cd "$d"
  ../musl-1.2.5/configure --target=riscv32 CC=clang \
    CFLAGS="--target=riscv32-linux-musl $ARCH -O2 $EXTRA" \
    LDFLAGS='-fuse-ld=lld -Wl,--unresolved-symbols=ignore-all' LIBCC=' ' > cfg.log 2>&1
  make -j4 lib/libc.so > build.log 2>&1
  llvm-objdump -d --no-show-raw-insn "$d/lib/libc.so" > "$d/dis.txt"
  llvm-objdump -d --no-show-raw-insn -M no-aliases "$d/lib/libc.so" > "$d/dis-noalias.txt"
  echo "DONE $t $(stat -c%s "$d/lib/libc.so")"
done
