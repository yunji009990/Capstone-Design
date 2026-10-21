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
    # Blender가 FBX의 상대 텍스처 경로를 해석할 때 작업 기준이 달라질 수 있다.
    src, texdir, dst = (os.path.abspath(path) for path in argv[:3])

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

    def load(name, required=False):
        path = os.path.join(texdir, name)
        if not os.path.isfile(path):
            if required:
                raise FileNotFoundError("몸체 재질에 필요한 텍스처가 없습니다: " + path)
            return None
        return bpy.data.images.load(path, check_existing=True)

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
            # 새 양손은 의상 atlas와 UV가 다르다. 모든 슬롯에 의상 이미지를
            # 덮어쓰면 손톱·피부와 검은 소매 안감이 사라지므로 재질별로 연결한다.
            is_hand = material.name.startswith("Natural_Hand_Male")
            is_cuff = material.name.startswith("Cuff_Inner_Black")
            color = load("Hands/MaleHands_Color.png", required=True) if is_hand else base
            roughness = rough
            if is_cuff:
                for socket_name in ("Base Color", "Roughness", "Normal"):
                    for link in list(bsdf.inputs[socket_name].links):
                        links.remove(link)
                bsdf.inputs["Base Color"].default_value = (.006, .007, .009, 1)
                bsdf.inputs["Roughness"].default_value = .86
                color, roughness = None, None
            if color is not None:
                node = nodes.new("ShaderNodeTexImage")
                node.image = color
                node.location = (-600, 300)
                links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
            if is_hand:
                node = nodes.new("ShaderNodeTexImage")
                node.image = load("Hands/MaleHands_MetalRoughness.png", required=True)
                node.image.colorspace_settings.name = "Non-Color"
                separate = nodes.new("ShaderNodeSeparateColor")
                links.new(node.outputs["Color"], separate.inputs["Color"])
                links.new(separate.outputs["Green"], bsdf.inputs["Roughness"])
                node = nodes.new("ShaderNodeTexImage")
                node.image = load("Hands/MaleHands_Normal.png", required=True)
                node.image.colorspace_settings.name = "Non-Color"
                normal = nodes.new("ShaderNodeNormalMap")
                normal.inputs["Strength"].default_value = .65
                links.new(node.outputs["Color"], normal.inputs["Color"])
                links.new(normal.outputs["Normal"], bsdf.inputs["Normal"])
                specular = bsdf.inputs.get("Specular IOR Level") or bsdf.inputs.get("Specular")
                if specular is not None:
                    specular.default_value = .28
                roughness = None
            if roughness is not None:
                node = nodes.new("ShaderNodeTexImage")
                node.image = roughness
                node.image.colorspace_settings.name = "Non-Color"
                node.location = (-600, 0)
                links.new(node.outputs["Color"], bsdf.inputs["Roughness"])
            bsdf.inputs["Metallic"].default_value = 0.0
            print(f"[prep] 머티리얼 {material.name} · base {bool(color)} · hand {is_hand} · cuff {is_cuff}")

    zs = [(o.matrix_world @ v.co).z for o in meshes for v in o.data.vertices]
    print(f"[prep] 키 {min(zs):.4f} ~ {max(zs):.4f} = {max(zs) - min(zs):.4f} m")

    # GLB exporter가 실제 재질에 연결된 이미지를 포함한다. FBX 임포트 중
    # 생긴 미사용 이미지의 깨진 상대 경로까지 pack_all로 읽지 않는다.
    export_options = dict(filepath=dst, export_format="GLB", export_yup=True,
                          export_animations=True, export_skins=True, export_extras=True)
    # glTFast 6.14.1의 다중 재질 스킨 로더가 같은 버퍼에 여러 가중치
    # 정규화 job을 겹쳐 예약하지 않도록 한다. 이전 exporter에는 이 옵션이 없다.
    if "export_shared_accessors" in bpy.ops.export_scene.gltf.get_rna_type().properties:
        export_options["export_shared_accessors"] = True
    bpy.ops.export_scene.gltf(**export_options)
    print("[done]", dst)


if __name__ == "__main__":
    main()
