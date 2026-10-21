"""연결된 몸체 제작 사본을 Unity용 FBX로 내보낸다 (Blender 배치).

blender --background --python-exit-code 1 --python tools/body_export.py -- <몸체.blend> <Human.fbx>
검토용 표시 메시와 액션을 제외하고, 실제 스킨 메시와 그 Armature만 선택한다.
"""
import sys
from pathlib import Path

import bpy


def main():
    src, dst = sys.argv[sys.argv.index("--") + 1:]
    bpy.ops.wm.open_mainfile(filepath=str(Path(src).resolve()))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"
              and any(mod.type == "ARMATURE" and mod.object for mod in obj.modifiers)]
    rigs = {mod.object for obj in meshes for mod in obj.modifiers
            if mod.type == "ARMATURE" and mod.object}
    if len(rigs) != 1 or not meshes:
        raise RuntimeError("단일 Armature에 연결된 몸체 메시가 필요합니다")
    rig = next(iter(rigs))
    rig.data.pose_position = "REST"
    for obj in [rig] + meshes:
        obj.animation_data_clear()
    for obj in meshes:
        if obj.data.shape_keys:
            obj.data.shape_keys.animation_data_clear()
            for key in obj.data.shape_keys.key_blocks:
                key.value = 0.0
    bpy.ops.object.select_all(action="DESELECT")
    for obj in [rig] + meshes:
        obj.hide_set(False)
        obj.select_set(True)
    bpy.context.view_layer.objects.active = rig
    output = Path(dst).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=str(output), use_selection=True, object_types={"ARMATURE", "MESH"},
        axis_forward="-Z", axis_up="Y", apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_UNITS", bake_space_transform=False,
        # modifier 평가 메시에는 Shape Key가 없어 FBX morph가 누락된다.
        # 형태 키가 있는 원본은 정점 순서·스킨·키를 그대로 내보낸다.
        use_mesh_modifiers=not any(obj.data.shape_keys for obj in meshes),
        mesh_smooth_type="FACE", add_leaf_bones=False,
        use_armature_deform_only=False, armature_nodetype="NULL",
        primary_bone_axis="Y", secondary_bone_axis="X", bake_anim=False,
        path_mode="AUTO", embed_textures=False,
    )
    print(f"[body-export] {output}: meshes={len(meshes)}, bones={len(rig.data.bones)}, "
          f"vertices={sum(len(obj.data.vertices) for obj in meshes)}")


if __name__ == "__main__":
    main()
