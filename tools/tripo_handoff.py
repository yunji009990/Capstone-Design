"""Package the current Tripo handoff baseline without changing Git's index or branch.

build --out FILE creates a code overlay for a clone at the recorded base commit.
check/apply FILE --repo DIR validates every affected file before writing anything.
Private settings, source photos, GLBs, caches and the experience scenes are excluded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DOCS = {
    "웹_등록_흐름_개선.md", "웹_설문_항목_사용현황.md",
    "웹_설문_v2_사용법.md", "웹_설문_사전지식_개편_기획.md",
    "AI_하네스.md", "대화_AI_개발가이드.md",
    "Tripo_팀원_AI_작업지시서.md", "Tripo_개발환경_실행가이드.md",
    "Tripo_모델_테스트_씬.md", "Tripo_T포즈_전처리_실험.md",
    "웹_Tripo_대화AI_서비스_분리.md", "AI_응답_테스트_씬.md", "서버_음성대화_구조.md",
    "Raon_작업이력_보관.md", "판정기_텍스트_검사.md", "설문_인물_3D_통합_설계안.md",
    "음성대화_작업현황.md", "Raon모델_분석.md", "실시간_텍스트대화_구현.md",
}


def selected(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        return False
    if name in {"README.md", "AGENTS.md", "CLAUDE.md", ".gitignore", "Packages/manifest.json", "Packages/packages-lock.json",
                ".claude/settings.json", ".claude/commands/check.md", ".claude/commands/eval.md",
                ".claude/commands/verify.md", ".claude/commands/impl.md",
                ".claude/agents/doc-scout.md", ".claude/agents/cross-check.md", ".github/workflows/checks.yml",
                "tools/check.py", "tools/tests/test_check.py", "Survey/README.md",
                "ProjectSettings/ProjectVersion.txt", "tools/README.md", "tools/service_bundle.py",
                "tools/eval_turn_judge.py", "tools/turn_judge_cases.json",
                "tools/tripo_trial.py", "tools/tripo_motion_pack.py", "tools/tripo_handoff.py",
                "tools/tests/test_tripo_handoff.py", "Survey/model_worker.py", "Survey/requirements-worker.txt",
                "Web/app.py", "Web/survey_v2.py", "Web/README.md", "Web/.env.example", "Web/requirements.txt",
                # 설문 v2로 대체된 v1 변환 모듈. 삭제가 전달되도록 범위에 남겨 둔다.
                "Web/persona.py", "Web/registration_input.py"}:
        return True
    if path.parent.as_posix() == "docs":
        return path.name in DOCS
    if name.startswith(("Assets/Editor/", "Assets/Scripts/")):
        if name.startswith("Assets/Scripts/Raon/Fonts"):
            return False
        return name.endswith((".cs", ".cs.meta", ".asmdef", ".asmdef.meta", ".asmref", ".asmref.meta")) or (
            name.endswith(".meta") and "." not in path.name[:-5])
    if path.parent.as_posix() == "Assets/Scenes":
        return path.name in {stem + ext for stem in ("AI_Response_Test", "Tripo_Model_Test", "Raon_Voice_Test")
                             for ext in (".unity", ".unity.meta")} or path.name in {
                                 "Tripo_Model_Test_Floor.mat", "Tripo_Model_Test_Floor.mat.meta"}
    if name.startswith(("Survey/core/", "Survey/tests/", "Server/")):
        return path.suffix in {".py", ".sh", ".md", ".txt"} or path.name.endswith(".env.example")
    if name.startswith("Web/static/"):
        # presets_v2.json은 화면과 검사가 함께 쓰는 가상 예시다. 참여자 자료가 아니다.
        return path.suffix in {".html", ".css", ".js"} or name == "Web/static/presets_v2.json"
    if name.startswith("Web/tests/"):
        return path.suffix in {".py", ".cjs"}
    return False


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return result.stdout


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def blob_hash(raw, algorithm):
    return hashlib.new(algorithm, b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def target_path(repo, name):
    if not selected(name):
        raise ValueError("Path outside the handoff scope: " + name)
    target = repo.joinpath(*PurePosixPath(name).parts)
    for parent in [target, *target.parents]:
        if parent == repo:
            break
        if parent.is_symlink():
            raise ValueError("Symlink in target path: " + name)
    if not target.resolve().is_relative_to(repo):
        raise ValueError("Target escapes the repository: " + name)
    return target


def build(repo, output):
    repo = repo.resolve()
    base = git(repo, "rev-parse", "HEAD").decode().strip()
    algorithm = git(repo, "rev-parse", "--show-object-format").decode().strip()
    before = {}
    for entry in git(repo, "ls-tree", "-rz", "--full-tree", "HEAD").split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        name = name.decode("utf-8")
        if selected(name) and kind == "blob":
            if mode not in {"100644", "100755"}:
                raise ValueError("Unsupported Git file mode: " + name)
            before[name] = {"before_blob": oid, "mode": mode}
    names = {n.decode("utf-8") for n in git(repo, "ls-files", "--cached", "--others",
             "--exclude-standard", "-z").split(b"\0") if n}
    manifest = {"schema_version": 1, "base_commit": base, "git_object_format": algorithm,
                "files": [], "deleted": [], "excluded": ["credentials", "photos", "GLBs", "runtime data",
                "Unity Library", "Scene_1/Scene_2 edits", "font atlas edits"]}
    output = output.resolve()
    if output.exists():
        raise ValueError("Output already exists; choose a new filename")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(n for n in names | set(before) if selected(n)):
            path = target_path(repo, name)
            previous = before.get(name, {"before_blob": None, "mode": "100644"})
            if not path.exists():
                if name in before:
                    manifest["deleted"].append({"path": name, **previous})
                continue
            if not path.is_file():
                continue
            raw = path.read_bytes()
            archive.writestr("code/" + name, raw)
            manifest["files"].append({"path": name, "sha256": sha256(raw), "bytes": len(raw), **previous})
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        archive.writestr("tripo_handoff.py", Path(__file__).read_bytes())
        archive.writestr("START_HERE.md", "# Tripo handoff\n\n"
            "Clone the project at base commit `" + base + "` in a separate folder.\n"
            "Close its Unity Editor before applying this overlay.\n\n"
            "```text\npython tripo_handoff.py check HANDOFF.zip --repo YOUR_CLONE\n"
            "python tripo_handoff.py apply HANDOFF.zip --repo YOUR_CLONE\n```\n\n"
            "Read `docs/Tripo_팀원_AI_작업지시서.md` and `docs/Tripo_개발환경_실행가이드.md` "
            "in the resulting project. These documents are also under `code/docs/` in this archive.\n\n"
            "The overlay includes shared compile dependencies as a baseline; the Tripo task document "
            "defines which files the teammate owns. Secrets and model media must be supplied separately.\n")
    return {"bundle": str(output), "base_commit": base, "files": len(manifest["files"]),
            "deletions": len(manifest["deleted"]), "bytes": output.stat().st_size}


def apply(repo, bundle, write=False):
    repo = repo.resolve(strict=True)
    if Path(git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve() != repo:
        raise ValueError("--repo must name the Git repository root")
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema_version") != 1:
            raise ValueError("Unsupported handoff format")
        if git(repo, "rev-parse", "HEAD").decode().strip() != manifest["base_commit"]:
            raise ValueError("Clone must be at the bundle's exact base commit")
        algorithm = git(repo, "rev-parse", "--show-object-format").decode().strip()
        if algorithm != manifest["git_object_format"]:
            raise ValueError("Git object format does not match")
        plan, seen = [], set()
        for remove, entries in ((False, manifest["files"]), (True, manifest["deleted"])):
            for item in entries:
                name = item["path"]
                if name in seen:
                    raise ValueError("Duplicate target: " + name)
                seen.add(name)
                path = target_path(repo, name)
                if path.exists() and not path.is_file():
                    raise ValueError("Target is not a regular file: " + name)
                raw = None if remove else archive.read("code/" + name)
                if raw is not None and (sha256(raw) != item["sha256"] or len(raw) != item["bytes"]):
                    raise ValueError("Payload checksum mismatch: " + name)
                current = path.read_bytes() if path.is_file() else None
                if current == raw:
                    continue
                if current is None:
                    if item["before_blob"] is not None:
                        raise ValueError("Expected base file is missing: " + name)
                elif blob_hash(current, algorithm) != item["before_blob"] and not (
                    b"\0" not in current and blob_hash(current.replace(b"\r\n", b"\n"), algorithm) == item["before_blob"]):
                    raise ValueError("Local edits would be overwritten: " + name)
                plan.append((path, raw, item["mode"]))
        # Every checksum, path and local modification is checked before the first write.
        if write:
            for path, raw, mode in plan:
                if raw is None:
                    path.unlink()  # Validated, individual file under the named repository.
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".handoff-", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(raw)
                try:
                    os.replace(temporary, path)
                    if os.name != "nt":
                        path.chmod(0o755 if mode == "100755" else 0o644)
                finally:
                    temporary.unlink(missing_ok=True)
        return {"status": "applied" if write else "checked", "changes": len(plan),
                "files": len(manifest["files"]), "deletions": len(manifest["deleted"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("build")
    pack.add_argument("--out", type=Path, required=True)
    for command in ("check", "apply"):
        sub = commands.add_parser(command)
        sub.add_argument("bundle", type=Path)
        sub.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(ROOT, args.out) if args.command == "build" else apply(
            args.repo, args.bundle, write=args.command == "apply")
        print(json.dumps(result, ensure_ascii=True))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
