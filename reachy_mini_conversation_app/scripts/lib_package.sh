#!/usr/bin/env bash
# Shared by robot_deploy.sh and eval/run_eval.sh. Needs REPO_ROOT, PKG_PATH_IN_REPO and die().

# Extract the package exactly as committed into $2, with Git LFS files (the wake-word
# model, sounds) resolved to their real content — git archive alone ships LFS pointers.
build_package() {
  local commit="$1" out="$2" f
  rm -rf "$out" && mkdir -p "$out"
  git -C "$REPO_ROOT" archive "$commit:$PKG_PATH_IN_REPO" | tar -x -C "$out"
  while IFS= read -r f; do
    git -C "$REPO_ROOT" show "$commit:$PKG_PATH_IN_REPO/$f" | git -C "$REPO_ROOT" lfs smudge -- "$f" >"$out/$f.real"
    mv "$out/$f.real" "$out/$f"
  done < <(cd "$out" && grep -rlI --exclude-dir=__pycache__ '^version https://git-lfs.github.com/spec/v1' . | sed 's#^\./##')
  if grep -rlI '^version https://git-lfs.github.com/spec/v1' "$out" >/dev/null; then
    die "unresolved Git LFS pointers in the package (run: git lfs pull)"
  fi
  test -s "$out/gemini_live.py" || die "package build for $commit looks empty"
}
