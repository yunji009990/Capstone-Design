"""고정 몸통 FBX 를 합치기에 쓸 GLB 로 만든다 (Blender 배치).

왜 필요한가
───────────
FBX 에는 텍스처가 실려 있지 않고(옆 폴더에 PNG 로 따로 있다), 임포트하면 아마추어에
센티미터 보정용 배율 0.01 이 남는다. 그 배율이 GLB 의 스켈레톤 루트 노드에 그대로
실려 나가는데, 유니티에서 그 노드가 휴머노이드 아바타의 뼈대 루트로 잡히면 아바타가
깨져 몸이 접힌다(PersonaArrival 이 스킨 메시에서 위로 올라가 그 노드를 잡는다).

그래서 여기서 배율을 적용해 없앤다. 내보낸 GLB 의 루트는 배율 1 이어야 한다.

    blender --background --python tools/body_prep.py -- <Human.fbx> <tex 폴더> <출력.glb>

작업 중 남은 스킨 없는 메시는 키 계산을 흐트러뜨리므로 빼고 내보낸다.
"""
import os
import sys

import bpy


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    src, texdir, dst = argv[0], argv[1], argv[2]

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=src)

    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    meshes = []
    for obj in [o for o in bpy.data.objects if o.type == "MESH"]:
        skinned = obj.vertex_groups and any(m.type == "ARMATURE" for m in obj.modifiers)
        if skinned:
            meshes.append(obj)
        else:
            print(f"[prep] 스킨 없는 메시 제외: {obj.name} ({len(obj.data.vertices)} verts)")
            bpy.data.objects.remove(obj, do_unlink=True)
    if not armatures or not meshes:
        raise SystemExit("아마추어나 스킨 메시를 찾지 못했습니다")
    armature = armatures[0]
    print(f"[prep] 메시 {len(meshes)} · 본 {len(armature.data.bones)} · 배율 {tuple(armature.scale)}")

    # 배율을 적용해 없앤다. 아마추어와 메시를 함께 선택해야 뼈와 정점이 같이 따라온다.
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes + [armature]:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    print(f"[prep] 적용 후 아마추어 배율 {tuple(armature.scale)}")

    def load(name):
        path = os.path.join(texdir, name)
        return bpy.data.images.load(path) if os.path.isfile(path) else None

    base, rough = load("avatar_BaseColor.png"), load("avatar_roughness.png")
    for obj in meshes:
        for slot in obj.material_slots:
            material = slot.material
            if material is None:
                continue
            material.use_nodes = True
            nodes, links = material.node_tree.nodes, material.node_tree.links
            bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
            if bsdf is None:
                continue
            if base is not None:
                node = nodes.new("ShaderNodeTexImage")
                node.image = base
                node.location = (-600, 300)
                links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
            if rough is not None:
                node = nodes.new("ShaderNodeTexImage")
                node.image = rough
                node.image.colorspace_settings.name = "Non-Color"
                node.location = (-600, 0)
                links.new(node.outputs["Color"], bsdf.inputs["Roughness"])
            bsdf.inputs["Metallic"].default_value = 0.0
            print(f"[prep] 머티리얼 {material.name} · base {bool(base)} · rough {bool(rough)}")

    zs = [(o.matrix_world @ v.co).z for o in meshes for v in o.data.vertices]
    print(f"[prep] 키 {min(zs):.4f} ~ {max(zs):.4f} = {max(zs) - min(zs):.4f} m")

    bpy.ops.file.pack_all()
    bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", export_yup=True,
                              export_animations=True, export_skins=True, export_extras=True)
    print("[done]", dst)


main()
