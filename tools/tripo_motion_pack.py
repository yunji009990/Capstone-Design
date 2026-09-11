"""Reuse a trial rig to download nine motions and build one local animated GLB.

Paid POSTs are recorded before submission and never retried automatically.
All model files, task responses and signed URLs stay in ignored tools/_work/.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

from tripo_trial import ROOT, TripoClient, glb_summary, read_key

MOTIONS = {
    "sit": "앉기", "look_around": "주변 둘러보기", "greet_01": "인사",
    "wave_goodbye_01": "작별인사", "agree": "동의", "clap": "박수",
    "hug": "포옹", "laugh_01": "웃기", "sob": "흐느끼기",
}


def save_json(path: Path, value: dict):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def emit(stage: str, **values):
    print(json.dumps({"stage": stage, **values}, ensure_ascii=True), flush=True)


def read_glb(path: Path):
    blob = path.read_bytes()
    if struct.unpack_from("<4sII", blob) != (b"glTF", 2, len(blob)):
        raise ValueError(f"Invalid GLB: {path.name}")
    offset, document, binary = 12, None, None
    while offset < len(blob):
        length, kind = struct.unpack_from("<II", blob, offset)
        chunk = blob[offset + 8:offset + 8 + length]
        if len(chunk) != length:
            raise ValueError("Truncated GLB chunk")
        if kind == 0x4E4F534A:
            document = json.loads(chunk)
        elif kind == 0x004E4942:
            binary = chunk
        offset += length + 8
    if document is None or binary is None or len(document.get("buffers", [])) != 1:
        raise ValueError("Expected one embedded GLB buffer")
    if "uri" in document["buffers"][0] or document.get("extensionsUsed"):
        raise ValueError("External buffers or extensions are unsupported by this trial merger")
    return document, binary


def node_paths(document: dict):
    nodes = document["nodes"]
    parents = {child: i for i, node in enumerate(nodes) for child in node.get("children", [])}

    def path(i, ancestors=()):
        if i in ancestors:
            raise ValueError("Cyclic node hierarchy")
        name = nodes[i].get("name")
        if not name:
            raise ValueError("Cannot safely map unnamed nodes")
        return (path(parents[i], ancestors + (i,)) + "/" if i in parents else "") + name

    result = {path(i): i for i in range(len(nodes))}
    if len(result) != len(nodes):
        raise ValueError("Ambiguous node hierarchy")
    return result


def merge_pack(directory: Path):
    # Keep geometry, materials, bind matrices and the rest pose from the original rig.
    base, buffer = read_glb(directory / "rigged.glb")
    binary = bytearray(buffer[:base["buffers"][0]["byteLength"]])
    base_paths = node_paths(base)
    base["animations"] = []
    catalog = []
    for motion, label in MOTIONS.items():
        source_path = directory / ("animated.glb" if motion == "sit" else f"animations/{motion}.glb")
        source, source_binary = read_glb(source_path)
        paths = node_paths(source)
        if set(paths) != set(base_paths):
            raise ValueError(f"{motion}: node hierarchy differs from the original rig")
        mapping = {index: base_paths[path] for path, index in paths.items()}
        for source_index, base_index in mapping.items():
            a, b = source["nodes"][source_index], base["nodes"][base_index]
            for key, default in (("translation", [0, 0, 0]), ("rotation", [0, 0, 0, 1]),
                                 ("scale", [1, 1, 1]), ("matrix", [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])):
                av, bv = a.get(key, default), b.get(key, default)
                delta = max(abs(x - y) for x, y in zip(av, bv))
                if key == "rotation":
                    delta = min(delta, max(abs(x + y) for x, y in zip(av, bv)))
                if delta > 1e-4:
                    raise ValueError(f"{motion}: rest transform changed on {a['name']} ({key}: {delta})")
        if len(source.get("skins", [])) != len(base.get("skins", [])):
            raise ValueError(f"{motion}: skin count changed")
        for a, b in zip(source["skins"], base["skins"]):
            if [mapping[j] for j in a["joints"]] != b["joints"]:
                raise ValueError(f"{motion}: joint order changed")
        if len(source.get("animations", [])) != 1:
            raise ValueError(f"{motion}: expected exactly one animation")
        animation = copy.deepcopy(source["animations"][0])
        original_name = animation.get("name", "")
        animation["name"] = motion
        accessor_map, view_map = {}, {}

        def copy_accessor(index):
            if index in accessor_map:
                return accessor_map[index]
            accessor = copy.deepcopy(source["accessors"][index])
            if "sparse" in accessor or "bufferView" not in accessor:
                raise ValueError("Sparse/unbuffered animation accessor is unsupported")
            view_index = accessor["bufferView"]
            if view_index not in view_map:
                view = copy.deepcopy(source["bufferViews"][view_index])
                if view.get("buffer", 0) != 0:
                    raise ValueError("Animation uses an external buffer")
                start, length = view.get("byteOffset", 0), view["byteLength"]
                data = source_binary[start:start + length]
                if len(data) != length:
                    raise ValueError("Animation buffer view is truncated")
                binary.extend(b"\0" * (-len(binary) % 4))
                view["byteOffset"], view["buffer"] = len(binary), 0
                binary.extend(data)
                view_map[view_index] = len(base["bufferViews"])
                base["bufferViews"].append(view)
            accessor["bufferView"] = view_map[view_index]
            accessor_map[index] = len(base["accessors"])
            base["accessors"].append(accessor)
            return accessor_map[index]

        for sampler in animation["samplers"]:
            sampler["input"] = copy_accessor(sampler["input"])
            sampler["output"] = copy_accessor(sampler["output"])
        for channel in animation["channels"]:
            if channel["target"].get("path") not in {"translation", "rotation", "scale"}:
                raise ValueError("Only skeletal animation channels are supported")
            channel["target"]["node"] = mapping[channel["target"]["node"]]
        base["animations"].append(animation)
        catalog.append({"clip": motion, "label": label, "preset": original_name,
                        "source_clip": original_name, "source": str(source_path.relative_to(directory)),
                        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest()})
    base["buffers"] = [{"byteLength": len(binary)}]
    encoded = json.dumps(base, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    binary.extend(b"\0" * (-len(binary) % 4))
    blob = (struct.pack("<4sII", b"glTF", 2, 28 + len(encoded) + len(binary)) +
            struct.pack("<II", len(encoded), 0x4E4F534A) + encoded +
            struct.pack("<II", len(binary), 0x004E4942) + binary)
    dest = directory / "animated_pack.glb"
    temp = dest.with_suffix(".partial")
    temp.write_bytes(blob)
    summary = glb_summary(temp)
    if len(summary["animations"]) != len(MOTIONS) or not all(a["varying_channels"] for a in summary["animations"]):
        raise ValueError("Merged pack is missing moving clips")
    temp.replace(dest)
    summary["file"] = dest.name
    save_json(directory / "motion_catalog.json", {"motions": catalog, "asset": summary})
    emit("pack_built", asset=summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial-dir", type=Path, required=True)
    parser.add_argument("--motion", choices=list(MOTIONS), help="Download only one motion as an API probe")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    directory = args.trial_dir.resolve(strict=True)
    if not directory.is_relative_to(ROOT / "tools" / "_work"):
        parser.error("The trial must be inside ignored tools/_work")
    trial = json.loads((directory / "trial.json").read_text(encoding="utf-8"))
    rig_id = trial["tasks"]["rig"]["task_id"]
    selected = [args.motion] if args.motion else list(MOTIONS)
    if args.dry_run:
        emit("dry_run", motions=selected, reuse_rig=True, reuse_sit=True,
             estimated_new_credits=10 * len([m for m in selected if m != "sit"]), network_calls=0)
        return
    if args.build_only:
        merge_pack(directory)
        return
    path = directory / "motion_tasks.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "rig_task_id": rig_id, "tasks": {}, "reused_sit_task_id": trial["tasks"]["animation"]["task_id"]}
    if manifest["rig_task_id"] != rig_id:
        raise ValueError("The motion manifest belongs to another rig")
    client = TripoClient(read_key())
    (directory / "animations").mkdir(exist_ok=True)
    for motion in selected:
        if motion == "sit":
            glb_summary(directory / "animated.glb")
            emit(motion, status="reused")
            continue
        dest = directory / "animations" / (motion + ".glb")
        record = manifest["tasks"].get(motion)
        if dest.exists() and record and record.get("asset"):
            if hashlib.sha256(dest.read_bytes()).hexdigest() != record["sha256"]:
                raise ValueError(f"{motion}: cached asset has changed")
            emit(motion, status="cached")
            continue
        if record is None:
            body = {"type": "animate_retarget", "original_model_task_id": rig_id,
                    "animation": "preset:biped:" + motion, "out_format": "glb", "bake_animation": True}
            record = manifest["tasks"][motion] = {"request": body, "state": "submitting"}
            save_json(path, manifest)
            # An interrupted POST has an unknown outcome: do not resubmit on restart.
            record["task_id"] = client._submit(body)
            record["state"] = "submitted"
            save_json(path, manifest)
            emit(motion, status="submitted", task_id=record["task_id"])
        if not record.get("task_id"):
            raise RuntimeError(f"{motion}: submission outcome is unknown; reconcile with Tripo before retrying")
        deadline, failures, previous = time.monotonic() + 900, 0, None
        while time.monotonic() < deadline:
            try:
                response = client.get_task(record["task_id"])
                failures = 0
            except Exception as exc:
                transient = isinstance(exc, OSError) or any(f"[{c}]" in str(exc) for c in (429, 500, 502, 503, 504))
                if not transient or failures >= 3:
                    raise
                failures += 1
                time.sleep(5)
                continue
            if response.get("code", 0) != 0:
                raise RuntimeError(f"{motion}: query failed with code {response.get('code')}")
            data = response.get("data") or {}
            record["response"] = data
            manifest["credits_consumed"] = sum(t.get("response", {}).get("consumed_credit", 0) or 0
                                                for t in manifest["tasks"].values())
            save_json(path, manifest)
            current = (data.get("status"), data.get("progress"))
            if current != previous:
                emit(motion, status=current[0], progress=current[1], credits=data.get("consumed_credit"))
                previous = current
            if current[0] == "success":
                break
            if current[0] in {"failed", "banned", "expired", "cancelled", "canceled", "unknown"}:
                raise RuntimeError(f"{motion}: task ended with {current[0]}")
            time.sleep(5)
        else:
            raise TimeoutError(f"{motion}: rerun to resume polling the same task")
        output = data.get("output") or {}
        url = output.get("pbr_model") or output.get("model")
        if isinstance(url, dict):
            url = url.get("url")
        if not url:
            raise RuntimeError(f"{motion}: task has no model URL")
        temp = dest.with_suffix(".partial")
        client.download_glb(url, temp)
        summary = glb_summary(temp)
        if not summary["skins"] or len(summary["animations"]) != 1 or not summary["animations"][0]["varying_channels"]:
            raise ValueError(f"{motion}: result has no skinned motion")
        temp.replace(dest)
        summary["file"] = dest.name
        record.update(state="downloaded", asset=summary, sha256=hashlib.sha256(dest.read_bytes()).hexdigest())
        save_json(path, manifest)
        emit(motion, status="downloaded", asset=summary)
    if args.motion is None:
        merge_pack(directory)
    emit("billing", new_credits_consumed=manifest.get("credits_consumed", 0))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
