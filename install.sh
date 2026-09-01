#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SOURCE_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
TEMPLATE_DIR="${SOURCE_DIR}/templates/project"
INSTALL_SUPPORT="${SOURCE_DIR}/factory/omf/install_support.py"
PYTHON="${OMF_INSTALL_PYTHON:-python3}"
PLAN=false
PROJECT_NAME=""
TARGET_ARGUMENT=""

usage() {
  cat <<'EOF'
Install Open Model Factory into a project directory.

Usage:
  ./install.sh [--plan] [--python EXECUTABLE] [--name PROJECT_NAME] DIRECTORY

Options:
  --plan                Show the complete installation plan without changing anything.
  --python EXECUTABLE   Python 3.11+ used to create DIRECTORY/.venv (default: python3).
  --name PROJECT_NAME   Name for a newly created project (default: directory basename).
  -h, --help            Show this help.

The installer preserves existing project files. It appends idempotent managed
sections to AGENTS.md and .gitignore, creates only missing OMF manifests, rebuilds
its managed .venv, initializes local .omf state, and runs omf doctor.
EOF
}

fail() {
  printf 'install.sh: %s\n' "$*" >&2
  exit 1
}

while (($# > 0)); do
  case "$1" in
    --plan)
      PLAN=true
      shift
      ;;
    --python)
      (($# >= 2)) || fail "--python requires an executable"
      PYTHON="$2"
      shift 2
      ;;
    --name)
      (($# >= 2)) || fail "--name requires a project name"
      PROJECT_NAME="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      (($# == 1)) || fail "exactly one directory is required"
      TARGET_ARGUMENT="$1"
      shift
      ;;
    -*)
      fail "unknown option: $1"
      ;;
    *)
      [[ -z "${TARGET_ARGUMENT}" ]] || fail "exactly one directory is required"
      TARGET_ARGUMENT="$1"
      shift
      ;;
  esac
done

[[ -n "${TARGET_ARGUMENT}" ]] || {
  usage >&2
  exit 2
}

for required in \
  "${SOURCE_DIR}/pyproject.toml" \
  "${INSTALL_SUPPORT}" \
  "${SOURCE_DIR}/requirements.build.lock" \
  "${SOURCE_DIR}/requirements.runtime.lock" \
  "${TEMPLATE_DIR}/AGENTS.md" \
  "${TEMPLATE_DIR}/gitignore" \
  "${TEMPLATE_DIR}/omf.yaml" \
  "${TEMPLATE_DIR}/bindings/local.yaml" \
  "${TEMPLATE_DIR}/policies/default.yaml"; do
  [[ -f "${required}" ]] || fail "installation source is incomplete: ${required}"
done

PYTHON_PATH="$(command -v "${PYTHON}")" \
  || fail "the requested Python interpreter is not executable: ${PYTHON}"
if [[ "${PYTHON_PATH}" != /* ]]; then
  PYTHON_PATH="$(CDPATH= cd -- "$(dirname -- "${PYTHON_PATH}")" && pwd -P)/$(basename -- "${PYTHON_PATH}")"
fi
[[ -x "${PYTHON_PATH}" && ! -d "${PYTHON_PATH}" ]] \
  || fail "the requested Python interpreter is not an executable file: ${PYTHON_PATH}"
PYTHON="${PYTHON_PATH}"
"${PYTHON}" -c '
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Open Model Factory requires Python 3.11 or newer")
' || fail "a working Python 3.11+ interpreter is required"

TARGET="$("${PYTHON}" - "${TARGET_ARGUMENT}" <<'PY'
import sys
from pathlib import Path

print(Path(sys.argv[1]).expanduser().resolve(strict=False))
PY
)"
[[ "${TARGET}" != "/" ]] || fail "refusing to install into the filesystem root"

derive_name() {
  local value
  value="$(basename -- "${TARGET}")"
  value="$(printf '%s' "${value}" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9.-]+/-/g; s/^[^a-z0-9]+//; s/[^a-z0-9]+$//' \
    | cut -c1-63 \
    | sed -E 's/[^a-z0-9]+$//')"
  printf '%s' "${value:-omf-project}"
}

if [[ -z "${PROJECT_NAME}" ]]; then
  PROJECT_NAME="$(derive_name)"
fi
if [[ ! "${PROJECT_NAME}" =~ ^[a-z0-9]([a-z0-9.-]{0,61}[a-z0-9])?$ ]]; then
  fail "project name must satisfy ^[a-z0-9]([a-z0-9.-]{0,61}[a-z0-9])?$"
fi

validate_managed_file() {
  local destination="$1"
  local begin_marker="$2"
  local end_marker="$3"

  [[ -e "${destination}" || -L "${destination}" ]] || return 0
  "${PYTHON}" "${INSTALL_SUPPORT}" validate \
    "${destination}" "${begin_marker}" "${end_marker}" \
    || fail "managed markers are malformed in ${destination}"
}

assert_safe_directory() {
  local path="$1"
  [[ ! -L "${path}" ]] || fail "refusing symbolic-link directory: ${path}"
  [[ ! -e "${path}" || -d "${path}" ]] || fail "expected a directory: ${path}"
}

ensure_directory() {
  local path="$1"
  assert_safe_directory "${path}"
  [[ -d "${path}" ]] || mkdir -- "${path}"
  assert_safe_directory "${path}"
}

reject_symbolic_link() {
  local path="$1"
  [[ ! -L "${path}" ]] || fail "refusing symbolic-link installer path: ${path}"
}

validate_managed_file \
  "${TARGET}/AGENTS.md" \
  '<!-- BEGIN OMF OPERATOR GUIDE -->' \
  '<!-- END OMF OPERATOR GUIDE -->'
validate_managed_file \
  "${TARGET}/.gitignore" \
  '# BEGIN OMF MANAGED IGNORE' \
  '# END OMF MANAGED IGNORE'

if [[ "${PLAN}" == true ]]; then
  cat <<EOF
Open Model Factory installation plan
target: ${TARGET}
python: ${PYTHON}
project name if created: ${PROJECT_NAME}

1. Create the target directory and create or rebuild its OMF-managed .venv.
2. Install hash-locked runtime and build dependencies as binary wheels.
3. Build this exact source without build isolation and install it into .venv.
4. Preserve an existing omf.yaml, or create a local project manifest.
5. Create missing workspace directories, local binding, and default policy.
6. Preserve and extend AGENTS.md and .gitignore with managed OMF sections.
7. Initialize Git only when the target is not already inside a repository.
8. Print and apply the repository-scoped local bootstrap plan under .omf/.
9. Require omf doctor and bounded agent context to succeed.

Network: pip may contact configured package indexes for hash-locked dependency
wheels. No OMF account is created, no project metadata is uploaded, and
telemetry call-home remains disabled.

No changes made.
EOF
  exit 0
fi

command -v git >/dev/null 2>&1 || fail "git is required to create and validate a workspace"
"${PYTHON}" -c 'import venv' || fail "the selected Python does not provide the venv module"

[[ -e "${TARGET}" ]] || mkdir -p -- "${TARGET}"
ensure_directory "${TARGET}"
exec {TARGET_FD}<"${TARGET}" || fail "could not anchor target directory ${TARGET}"
if [[ -e "/proc/self/fd/${TARGET_FD}" ]]; then
  TARGET_ANCHOR="/proc/self/fd/${TARGET_FD}"
elif [[ -e "/dev/fd/${TARGET_FD}" ]]; then
  TARGET_ANCHOR="/dev/fd/${TARGET_FD}"
else
  fail "the host does not expose inherited file descriptors through procfs or devfs"
fi
"${PYTHON}" - "${TARGET_FD}" "${TARGET}" <<'PY' \
  || fail "installer target changed while it was being anchored"
import os
import stat
import sys

anchored = os.fstat(int(sys.argv[1]))
named = os.stat(sys.argv[2], follow_symlinks=False)
if (
    not stat.S_ISDIR(anchored.st_mode)
    or not stat.S_ISDIR(named.st_mode)
    or (anchored.st_dev, anchored.st_ino) != (named.st_dev, named.st_ino)
):
    raise SystemExit("target descriptor does not match the named directory")
PY

for existing_directory in \
  "${TARGET_ANCHOR}/connectors" \
  "${TARGET_ANCHOR}/data" \
  "${TARGET_ANCHOR}/deployments" \
  "${TARGET_ANCHOR}/bindings" \
  "${TARGET_ANCHOR}/policies" \
  "${TARGET_ANCHOR}/workloads" \
  "${TARGET_ANCHOR}/modules"; do
  assert_safe_directory "${existing_directory}"
done
for protected_path in \
  "${TARGET_ANCHOR}/.git" \
  "${TARGET_ANCHOR}/.omf" \
  "${TARGET_ANCHOR}/.omf/metadata.db" \
  "${TARGET_ANCHOR}/.omf/identity" \
  "${TARGET_ANCHOR}/.omf/identity/signing.key" \
  "${TARGET_ANCHOR}/.omf/identity/secrets.key" \
  "${TARGET_ANCHOR}/.omf/store" \
  "${TARGET_ANCHOR}/.omf/runs" \
  "${TARGET_ANCHOR}/.omf/packages" \
  "${TARGET_ANCHOR}/.omf/telemetry" \
  "${TARGET_ANCHOR}/.omf/operations"; do
  reject_symbolic_link "${protected_path}"
done

VENV_DISPLAY="${TARGET}/.venv"
VENV_RELATIVE=".venv"
VENV="${TARGET_ANCHOR}/${VENV_RELATIVE}"
"${PYTHON}" - "${TARGET_FD}" <<'PY' \
  || fail "${VENV_DISPLAY} is not OMF-managed; move it aside before installation"
import os
import stat
import sys

no_follow = getattr(os, "O_NOFOLLOW", 0)
directory = os.dup(int(sys.argv[1]))


def require_marker(environment: int) -> None:
    marker = os.open(
        ".omf-managed", os.O_RDONLY | os.O_NONBLOCK | no_follow, dir_fd=environment
    )
    try:
        expected = b"open-model-factory managed environment\n"
        if not stat.S_ISREG(os.fstat(marker).st_mode) or os.read(marker, len(expected) + 1) != expected:
            raise SystemExit("managed marker is invalid")
    finally:
        os.close(marker)


try:
    try:
        current = os.stat(".venv", dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        current = None
    if current is not None and stat.S_ISDIR(current.st_mode):
        environment = os.open(
            ".venv", os.O_RDONLY | os.O_DIRECTORY | no_follow, dir_fd=directory
        )
        try:
            require_marker(environment)
        finally:
            os.close(environment)
    elif current is not None and stat.S_ISLNK(current.st_mode):
        target = os.readlink(".venv", dir_fd=directory)
        prefix = ".omf-venvs/"
        name = target.removeprefix(prefix)
        if not target.startswith(prefix) or not name or "/" in name or name in {".", ".."}:
            raise SystemExit("managed environment link has an invalid target")
        root = os.open(".omf-venvs", os.O_RDONLY | os.O_DIRECTORY | no_follow, dir_fd=directory)
        try:
            environment = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | no_follow, dir_fd=root
            )
            try:
                require_marker(environment)
            finally:
                os.close(environment)
        finally:
            os.close(root)
    elif current is not None:
        raise SystemExit("managed environment path is neither a directory nor a symbolic link")
finally:
    os.close(directory)
PY

ensure_directory "${TARGET_ANCHOR}/.omf-venvs"
NEW_VENV_NAME="$("${PYTHON}" - "${TARGET_FD}" <<'PY'
import os
import secrets
import sys

no_follow = getattr(os, "O_NOFOLLOW", 0)
target = os.dup(int(sys.argv[1]))
root = os.open(".omf-venvs", os.O_RDONLY | os.O_DIRECTORY | no_follow, dir_fd=target)
try:
    while True:
        name = f"venv.{secrets.token_hex(8)}"
        try:
            os.mkdir(name, 0o700, dir_fd=root)
        except FileExistsError:
            continue
        os.fsync(root)
        print(name)
        break
finally:
    os.close(root)
    os.close(target)
PY
)"
NEW_VENV_RELATIVE=".omf-venvs/${NEW_VENV_NAME}"
NEW_VENV="${TARGET_ANCHOR}/${NEW_VENV_RELATIVE}"
cleanup_new_venv() {
  [[ -z "${NEW_VENV:-}" || ! -e "${NEW_VENV}" ]] || \
    "${PYTHON}" - "${TARGET_FD}" "${NEW_VENV_NAME}" <<'PY'
import os
import shutil
import stat
import sys

no_follow = getattr(os, "O_NOFOLLOW", 0)
target = os.dup(int(sys.argv[1]))
directory = os.open(".omf-venvs", os.O_RDONLY | os.O_DIRECTORY | no_follow, dir_fd=target)
try:
    try:
        active = os.stat(".venv", dir_fd=target, follow_symlinks=False)
    except FileNotFoundError:
        active = None
    if active is not None and stat.S_ISLNK(active.st_mode):
        if os.readlink(".venv", dir_fd=target) == f".omf-venvs/{sys.argv[2]}":
            raise SystemExit(0)
    current = os.stat(sys.argv[2], dir_fd=directory, follow_symlinks=False)
    if not stat.S_ISDIR(current.st_mode):
        raise SystemExit("temporary environment is no longer a directory")
    shutil.rmtree(sys.argv[2], dir_fd=directory)
    os.fsync(directory)
finally:
    os.close(directory)
    os.close(target)
PY
}
trap cleanup_new_venv EXIT
run_in_target() {
  (cd -- "${TARGET_ANCHOR}" && "$@")
}
run_new_venv_python() {
  "${PYTHON}" -c '
import os
import sys

executable = sys.argv[2]
os.fchdir(int(sys.argv[1]))
os.execv(executable, [executable, *sys.argv[3:]])
' "${TARGET_FD}" "${NEW_VENV_RELATIVE}/bin/python" "$@"
}

printf 'Creating fresh isolated environment for %s\n' "${VENV_DISPLAY}"
run_in_target "${PYTHON}" -m venv "${NEW_VENV_RELATIVE}"
NEW_VENV_PYTHON="${NEW_VENV}/bin/python"
assert_safe_directory "${NEW_VENV}"
assert_safe_directory "${NEW_VENV}/bin"
[[ -f "${NEW_VENV}/pyvenv.cfg" && ! -L "${NEW_VENV}/pyvenv.cfg" ]] \
  || fail "fresh environment has no regular pyvenv.cfg"
[[ -L "${NEW_VENV_PYTHON}" ]] \
  || fail "${NEW_VENV_PYTHON} must be the venv-created symbolic link"
BASE_PYTHON="$("${PYTHON}" -c 'import sys; from pathlib import Path; print(Path(sys.executable).resolve())')"
LINKED_PYTHON="$("${PYTHON}" - "${NEW_VENV_PYTHON}" <<'PY'
import sys
from pathlib import Path

print(Path(sys.argv[1]).resolve(strict=True))
PY
)"
[[ "${LINKED_PYTHON}" == "${BASE_PYTHON}" ]] \
  || fail "fresh venv Python does not resolve to the selected interpreter"
run_new_venv_python - "${NEW_VENV}" <<'PY' \
  || fail "fresh environment failed virtual-environment validation"
import sys
from pathlib import Path

expected = Path(sys.argv[1]).resolve()
if Path(sys.prefix).resolve() != expected or sys.prefix == sys.base_prefix:
    raise SystemExit("interpreter is not isolated in the requested virtual environment")
if sys.version_info < (3, 11):
    raise SystemExit("the fresh virtual environment uses Python older than 3.11")
PY
run_new_venv_python -m pip --version >/dev/null \
  || fail "fresh environment does not provide pip"

printf 'Installing locked Open Model Factory runtime and build tooling\n'
PIP_DISABLE_PIP_VERSION_CHECK=1 run_new_venv_python -m pip install \
  --only-binary=:all: --require-hashes -r "${SOURCE_DIR}/requirements.runtime.lock"
PIP_DISABLE_PIP_VERSION_CHECK=1 run_new_venv_python -m pip install \
  --only-binary=:all: --require-hashes -r "${SOURCE_DIR}/requirements.build.lock"
PIP_DISABLE_PIP_VERSION_CHECK=1 run_new_venv_python -m pip install \
  --no-build-isolation --no-deps "${SOURCE_DIR}"
[[ -x "${NEW_VENV}/bin/omf" ]] \
  || fail "installation completed without an omf executable"
printf 'open-model-factory managed environment\n' >"${NEW_VENV}/.omf-managed"

OLD_VENV_CLEANUP="$(
  "${PYTHON}" - "${TARGET_FD}" "${NEW_VENV_NAME}" <<'PY'
import os
import secrets
import stat
import sys
from contextlib import suppress

fresh_name = sys.argv[2]
no_follow = getattr(os, "O_NOFOLLOW", 0)
directory = os.dup(int(sys.argv[1]))
root = os.open(".omf-venvs", os.O_RDONLY | os.O_DIRECTORY | no_follow, dir_fd=directory)
backup_name = f".venv.omf-old-{secrets.token_hex(8)}"
temporary_link = f".venv.omf-link-{secrets.token_hex(8)}"
old_moved = False
move_old = False
old_environment_name = None
link_created = False
committed = False


def require_marker(environment: int) -> None:
    marker = os.open(
        ".omf-managed", os.O_RDONLY | os.O_NONBLOCK | no_follow, dir_fd=environment
    )
    try:
        expected = b"open-model-factory managed environment\n"
        if not stat.S_ISREG(os.fstat(marker).st_mode) or os.read(marker, len(expected) + 1) != expected:
            raise SystemExit("managed marker is invalid")
    finally:
        os.close(marker)


def linked_environment_name() -> str:
    target = os.readlink(".venv", dir_fd=directory)
    prefix = ".omf-venvs/"
    name = target.removeprefix(prefix)
    if not target.startswith(prefix) or not name or "/" in name or name in {".", ".."}:
        raise SystemExit("existing .venv has an invalid managed target")
    environment = os.open(name, os.O_RDONLY | os.O_DIRECTORY | no_follow, dir_fd=root)
    try:
        require_marker(environment)
    finally:
        os.close(environment)
    return name


try:
    fresh = os.stat(fresh_name, dir_fd=root, follow_symlinks=False)
    if not stat.S_ISDIR(fresh.st_mode):
        raise SystemExit("fresh environment is not a directory")
    fresh_environment = os.open(
        fresh_name, os.O_RDONLY | os.O_DIRECTORY | no_follow, dir_fd=root
    )
    try:
        require_marker(fresh_environment)
    finally:
        os.close(fresh_environment)
    try:
        current = os.stat(".venv", dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        current = None
    if current is not None and stat.S_ISLNK(current.st_mode):
        old_environment_name = linked_environment_name()
    elif current is not None and stat.S_ISDIR(current.st_mode):
        current_directory = os.open(
            ".venv", os.O_RDONLY | os.O_DIRECTORY | no_follow, dir_fd=directory
        )
        try:
            require_marker(current_directory)
            observed = os.stat(".venv", dir_fd=directory, follow_symlinks=False)
            if (observed.st_dev, observed.st_ino) != (current.st_dev, current.st_ino):
                raise SystemExit("existing .venv changed during installation")
        finally:
            os.close(current_directory)
        move_old = True
    elif current is not None:
        raise SystemExit("existing .venv is not an OMF-managed environment")
    os.symlink(f".omf-venvs/{fresh_name}", temporary_link, dir_fd=directory)
    link_created = True
    try:
        os.fsync(directory)
        if move_old:
            os.rename(".venv", backup_name, src_dir_fd=directory, dst_dir_fd=directory)
            old_moved = True
        os.replace(temporary_link, ".venv", src_dir_fd=directory, dst_dir_fd=directory)
        link_created = False
    except Exception:
        if old_moved:
            os.rename(backup_name, ".venv", src_dir_fd=directory, dst_dir_fd=directory)
        raise
    committed = True
    with suppress(OSError):
        os.fsync(directory)
finally:
    if link_created:
        with suppress(FileNotFoundError):
            os.unlink(temporary_link, dir_fd=directory)
    with suppress(OSError):
        os.close(root)
    with suppress(OSError):
        os.close(directory)
if committed:
    if old_environment_name is not None and old_environment_name != fresh_name:
        print(f"root:{old_environment_name}")
    elif old_moved:
        print(f"target:{backup_name}")
    else:
        print("none:")
PY
  )" || fail "could not atomically activate the fresh OMF environment"
NEW_VENV=""

if [[ "${OLD_VENV_CLEANUP}" != "none:" ]]; then
  "${PYTHON}" - "${TARGET_FD}" "${OLD_VENV_CLEANUP}" <<'PY' \
    || printf 'install.sh: warning: previous OMF environment cleanup was deferred\n' >&2
import os
import shutil
import sys

no_follow = getattr(os, "O_NOFOLLOW", 0)
directory = os.dup(int(sys.argv[1]))
location, name = sys.argv[2].split(":", 1)
if not name or "/" in name or name in {".", ".."}:
    raise SystemExit("invalid cleanup name")
try:
    if location == "root":
        if not name.startswith("venv."):
            raise SystemExit("invalid versioned environment cleanup name")
        root = os.open(
            ".omf-venvs", os.O_RDONLY | os.O_DIRECTORY | no_follow, dir_fd=directory
        )
        try:
            shutil.rmtree(name, dir_fd=root)
            os.fsync(root)
        finally:
            os.close(root)
    elif location == "target":
        if not name.startswith(".venv.omf-old-"):
            raise SystemExit("invalid migrated environment cleanup name")
        shutil.rmtree(name, dir_fd=directory)
        os.fsync(directory)
    else:
        raise SystemExit("invalid cleanup location")
finally:
    os.close(directory)
PY
fi
run_active_venv_python() {
  "${PYTHON}" -c '
import os
import sys

executable = sys.argv[2]
os.fchdir(int(sys.argv[1]))
os.execv(executable, [executable, *sys.argv[3:]])
' "${TARGET_FD}" "${VENV_RELATIVE}/bin/python" "$@"
}

render_template() {
  local source="$1"
  local destination="$2"
  local name="$3"
  local namespace="$4"

  run_active_venv_python "${INSTALL_SUPPORT}" render \
    "${source}" "${destination}" "${name}" "${namespace}" \
    || fail "could not safely render manifest ${destination}"
}

upsert_managed_section() {
  local source="$1"
  local destination="$2"
  local begin_marker="$3"
  local end_marker="$4"

  "${PYTHON}" "${INSTALL_SUPPORT}" upsert \
    "${source}" "${destination}" "${begin_marker}" "${end_marker}" \
    || fail "could not atomically update managed guidance in ${destination}"
}

if [[ ! -e "${TARGET_ANCHOR}/omf.yaml" && ! -L "${TARGET_ANCHOR}/omf.yaml" ]]; then
  render_template \
    "${TEMPLATE_DIR}/omf.yaml" \
    "${TARGET_ANCHOR}/omf.yaml" \
    "${PROJECT_NAME}" \
    "local/${PROJECT_NAME}"
fi
[[ -f "${TARGET_ANCHOR}/omf.yaml" && ! -L "${TARGET_ANCHOR}/omf.yaml" ]] \
  || fail "refusing non-regular or symbolic-link project manifest: ${TARGET}/omf.yaml"

PROJECT_METADATA="$(run_active_venv_python - "${TARGET_ANCHOR}/omf.yaml" <<'PY'
import sys
from pathlib import Path

from omf.schema_registry import default_registry

project = default_registry.load(Path(sys.argv[1]))
if project["kind"] != "Project":
    raise SystemExit("omf.yaml must contain a Project resource")
print(project["metadata"]["name"] + "\t" + project["metadata"]["namespace"])
PY
)" || fail "existing omf.yaml is not a valid OMF Project"
IFS=$'\t' read -r PROJECT_NAME PROJECT_NAMESPACE <<<"${PROJECT_METADATA}"

for directory in \
  connectors \
  data \
  deployments \
  bindings \
  policies \
  workloads \
  modules; do
  ensure_directory "${TARGET_ANCHOR}/${directory}"
done
for directory in models objectives transforms generators trainers inference environments evaluators; do
  ensure_directory "${TARGET_ANCHOR}/modules/${directory}"
done

render_template \
  "${TEMPLATE_DIR}/bindings/local.yaml" \
  "${TARGET_ANCHOR}/bindings/local.yaml" \
  "${PROJECT_NAME}" \
  "${PROJECT_NAMESPACE}"
render_template \
  "${TEMPLATE_DIR}/policies/default.yaml" \
  "${TARGET_ANCHOR}/policies/default.yaml" \
  "${PROJECT_NAME}" \
  "${PROJECT_NAMESPACE}"
upsert_managed_section \
  "${TEMPLATE_DIR}/AGENTS.md" \
  "${TARGET_ANCHOR}/AGENTS.md" \
  '<!-- BEGIN OMF OPERATOR GUIDE -->' \
  '<!-- END OMF OPERATOR GUIDE -->'
upsert_managed_section \
  "${TEMPLATE_DIR}/gitignore" \
  "${TARGET_ANCHOR}/.gitignore" \
  '# BEGIN OMF MANAGED IGNORE' \
  '# END OMF MANAGED IGNORE'

reject_symbolic_link "${TARGET_ANCHOR}/.git"
if ! git -C "${TARGET_ANCHOR}" rev-parse --show-toplevel >/dev/null 2>&1; then
  printf 'Initializing Git repository\n'
  git init -q "${TARGET_ANCHOR}"
fi

for protected_path in \
  "${TARGET_ANCHOR}/.omf" \
  "${TARGET_ANCHOR}/.omf/metadata.db" \
  "${TARGET_ANCHOR}/.omf/identity" \
  "${TARGET_ANCHOR}/.omf/identity/signing.key" \
  "${TARGET_ANCHOR}/.omf/identity/secrets.key" \
  "${TARGET_ANCHOR}/.omf/store" \
  "${TARGET_ANCHOR}/.omf/runs" \
  "${TARGET_ANCHOR}/.omf/packages" \
  "${TARGET_ANCHOR}/.omf/telemetry" \
  "${TARGET_ANCHOR}/.omf/operations"; do
  reject_symbolic_link "${protected_path}"
done
printf 'Planning repository-scoped bootstrap\n'
run_active_venv_python -m omf --project "${TARGET_ANCHOR}" --output json bootstrap --plan
printf 'Applying repository-scoped bootstrap\n'
run_active_venv_python -m omf --project "${TARGET_ANCHOR}" --output json bootstrap

DOCTOR_OUTPUT="$(run_active_venv_python -m omf --project "${TARGET_ANCHOR}" --output json doctor)"
printf '%s\n' "${DOCTOR_OUTPUT}"
printf '%s' "${DOCTOR_OUTPUT}" | run_active_venv_python -c '
import json
import sys

result = json.load(sys.stdin)
if not result.get("ready"):
    raise SystemExit("omf doctor reported a non-ready installation")
' || fail "installed factory is not ready; inspect the doctor output above"

run_active_venv_python -m omf \
  --project "${TARGET_ANCHOR}" --output json agent context --limit 5 >/dev/null

"${PYTHON}" - "${TARGET_FD}" "${TARGET}" <<'PY' \
  || fail "target pathname changed during installation; use the anchored project location"
import os
import stat
import sys

anchored = os.fstat(int(sys.argv[1]))
named = os.stat(sys.argv[2], follow_symlinks=False)
if (
    not stat.S_ISDIR(named.st_mode)
    or (anchored.st_dev, anchored.st_ino) != (named.st_dev, named.st_ino)
):
    raise SystemExit("target pathname no longer identifies the anchored directory")
PY

cat <<EOF

Open Model Factory is ready at ${TARGET}

Activate the environment:
  . "${TARGET}/.venv/bin/activate"

First agent command:
  omf --project "${TARGET}" --output json agent context

Review AGENTS.md, set an attributable --actor identity for mutations, and commit
desired state before admitting a workload. Runtime state remains under .omf/.
EOF
