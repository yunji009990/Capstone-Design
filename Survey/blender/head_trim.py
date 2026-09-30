"""머리 GLB 에서 머리카락·얼굴·목만 남기고 나머지를 잘라 낸다 (Blender 배치).

왜 필요한가
───────────
컷아웃으로 사진의 옷을 지워도 Tripo 가 옷을 다시 만들어 낸다. image_to_model 은 닫힌
형상을 내놓아야 하므로 목 아래를 무언가로 끝내야 하고, 무엇으로 끝낼지는 생성 모델이
정하기 때문이다. 사진에서 지운 자리가 클수록 크게 지어낸다 — 정사각 프레임(head02)에서는
옷깃만 생겼지만 세로로 긴 프레임(head03)에서는 흰 상의가 통째로 생겼다.

사진 쪽에서 막기는 어렵다. 만든 뒤에 자르는 편이 확실하고 크레딧도 들지 않는다.

여기까지 오면서 틀렸던 것들
───────────────────────────
1. 가로줄 하나로 전부 잘라 **머리카락까지 잘렸다**. 목 높이에서 자르면 그 높이의
   머리카락도 없어진다. → 머리카락은 색으로 가려 높이와 무관하게 남긴다.
2. 면 중심이 기준 아래면 지우는 방식이라 **단면이 톱니**가 됐다. 면이 쪼개지지 않고
   통째로 남거나 지워지기 때문이다. → bisect_plane 으로 평면에서 실제로 쪼갠다.
3. 머리카락의 **하이라이트(윤기)가 밝게 찍혀** 머리카락이 아닌 것으로 갈렸다. 그대로
   자르면 머리에 구멍이 뚫린다. → 이웃 면으로 판정을 넓혀 메운다.
4. 자를 높이를 손으로 정했더니 모델마다 지어낸 옷의 크기가 달라 **맞지 않았다**.
   head02 는 0.16 이 맞았는데 head03 은 0.40 이 필요했다. → 밝은 면의 높이 분포에서
   옷이 끝나는 지점을 찾아 자동으로 정한다.
5. 열린 껍데기인 머리카락이 **뒷면 컬링으로 뚫려 보였다**(원본 경계 변 7,721개).
   → 재질을 양면으로 바꿔 내보낸다.

    blender --background --python Survey/blender/head_trim.py -- \
        <입력.glb> <출력.glb> [자를높이비율] [옵션]

자를높이비율을 주지 않으면 자동으로 찾는다. 준 값이 우선이다.

    --no-fill        자른 자리를 메우지 않는다 (몸통에 파묻을 때는 이쪽이 낫다)
    --single-sided   재질을 단면으로 둔다 (기본은 양면)
    --hair-max 0.35  이보다 어두우면 머리카락
    --white-min 0.75 이보다 밝으면 옷 후보
    --pct 99.9       옷의 윗끝으로 볼 높이 백분위. 칼라가 남으면 올리고,
                     목이 너무 짧으면 낮춘다
    --grow 4         머리카락 판정을 이웃으로 넓히는 횟수
"""
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Vector

HAIR_MAX = 0.35      # 베이스컬러 휘도가 이보다 어두우면 머리카락.
                     # 0.20 은 빡빡해서 머리 덩어리 일부가 옷으로 갈렸고,
                     # 자르고 나면 아래쪽 머리가 너덜너덜해졌다(head03 실측).
                     # 피부는 0.5~0.8 이라 0.35 까지 올려도 안 섞인다.
WHITE_MIN = 0.75     # 이보다 밝으면 옷 후보. 흰 옷 0.85~1.0, 피부 0.5~0.8
NEUTRAL_MAX = 0.10   # 옷 후보는 무채색이어야 한다. 밝기만으로는 창백한 피부를
                     # 흰 옷과 가르지 못한다 — 스튜디오 증명사진의 피부는 휘도
                     # 0.95 까지 올라간다. 색기로는 확연히 갈린다.
                     # 실측(99cd66fc): 흰 옷 채도 0.002 / 피부 채도 0.24
GROW = 4             # 머리카락 판정을 이웃으로 넓히는 횟수
PROBE = 256          # 텍스처를 이 크기로 줄여 찍는다. 색만 보면 되므로 충분하다
PERCENTILE = 99.9    # 옷으로 잡힌 면들의 높이 중 이 백분위를 옷의 윗끝으로 본다.
                     # 칼라 띠는 얇아서 밝은 면 전체의 몇 %밖에 안 된다 —
                     # head03 실측: 97백분위 -0.135(칼라 남음) / 99.9백분위 -0.047(제거)
MAX_FRACTION = 0.55  # 자동 탐지가 이보다 위를 가리키면 상한을 쓴다 (목까지 날리지 않게)
FALLBACK = 0.16      # 옷을 못 찾았을 때 쓸 비율


def base_color_pixels(obj):
    """이 오브젝트의 베이스컬러 텍스처를 작게 줄여 (PROBE, PROBE, 4) 배열로 돌려준다."""
    for slot in obj.material_slots:
        material = slot.material
        if material is None or not material.use_nodes:
            continue
        # 노드를 `is` 로 맞추면 안 된다. bpy 는 접근할 때마다 새 파이썬 래퍼를 만들어
        # 같은 노드라도 동일성 비교가 실패한다. 소켓 이름만 보고 고른다.
        for link in material.node_tree.links:
            if link.to_socket.name != "Base Color":
                continue
            if link.to_node.type != "BSDF_PRINCIPLED":
                continue
            image = getattr(link.from_node, "image", None)
            if image is None:
                continue
            # 원본을 줄이면 그대로 내보내지므로 사본을 줄인다.
            small = image.copy()
            small.scale(PROBE, PROBE)
            pixels = np.array(small.pixels[:], dtype=np.float32).reshape(PROBE, PROBE, 4)
            bpy.data.images.remove(small)
            return pixels
    return None


def face_colour(mesh, uv_layer, pixels):
    """면마다 UV 중심의 베이스컬러를 찍어 (면수, 3) 으로 돌려준다. 선형 공간이다.

    휘도와 채도를 따로 찍지 않고 여기서 한 번만 찍는다. 같은 면을 두 군데서
    표본하면 UV 뒤집기 같은 사소한 차이로 둘이 어긋나고, 그러면 두 값을 함께
    쓰는 판정이 조용히 틀린다."""
    if pixels is None or uv_layer is None:
        return None
    values = np.empty((len(mesh.faces), 3), dtype=np.float32)
    for face in mesh.faces:
        u = sum(loop[uv_layer].uv.x for loop in face.loops) / len(face.loops)
        v = sum(loop[uv_layer].uv.y for loop in face.loops) / len(face.loops)
        x = int(np.clip(u % 1.0, 0, 0.9999) * PROBE)
        y = int(np.clip(v % 1.0, 0, 0.9999) * PROBE)
        values[face.index] = pixels[y, x, :3]
    return values


def luminance(rgb):
    if rgb is None:
        return None
    return 0.2126 * rgb[:, 0] + 0.7152 * rgb[:, 1] + 0.0722 * rgb[:, 2]


def saturation(rgb):
    """HSV 채도. 흰·회색 옷은 0 에 가깝고, 피부는 붉은 기가 있어 0.2 를 넘는다."""
    if rgb is None:
        return None
    top = rgb.max(axis=1)
    return np.where(top > 1e-6, (top - rgb.min(axis=1)) / np.maximum(top, 1e-6), 0.0)


def hair_faces(mesh, luma, grow):
    """어두운 면을 머리카락으로 잡고 이웃으로 넓힌다.

    색만 보면 머리카락의 하이라이트가 밝게 찍혀 머리카락이 아닌 것으로 갈린다. 그대로
    자르면 머리 속에 구멍이 뚫리고 흰 점이 남는다. 이웃 면의 절반 이상이 머리카락이면
    그 면도 머리카락으로 본다. 점처럼 흩어진 오분류는 메워지고, 진짜 경계(피부·옷)는
    이웃이 부족해 번지지 않는다.
    """
    if luma is None:
        return set()
    found = {f.index for f in mesh.faces if luma[f.index] < HAIR_MAX}
    seeded = len(found)
    for _ in range(max(0, grow)):
        added = set()
        for face in mesh.faces:
            if face.index in found:
                continue
            near = [o.index for e in face.edges for o in e.link_faces if o.index != face.index]
            if near and sum(1 for n in near if n in found) * 2 >= len(near):
                added.add(face.index)
        if not added:
            break
        found |= added
    print(f"[trim] 머리카락 색으로 {seeded} -> 이웃으로 넓혀 {len(found)}")
    return found


def narrowest_ring(mesh, lo, hi):
    """5~55% 구간에서 가로 단면이 가장 가는 높이를 돌려준다. 색을 보지 않는다.

    흉상의 목은 옷(어깨)과 턱 사이에서 가장 가늘다. 색 판정과 완전히 다른 잣대라,
    둘이 서로를 검산한다. 실측: 현재 머리 21% / head01 18% / head02 20% /
    head03 51% / bd290323 25% — 색 판정이 내놓은 값과 같은 자리를 가리킨다.

    이 값은 상한으로만 쓴다. 더 아래에서 자르면 옷이 조금 남을 뿐이고 그건 몸통
    깃 안으로 들어가 보이지 않지만, 이 위에서 자르면 얼굴이 잘려 나간다.
    """
    z = np.array([v.co.z for v in mesh.verts], dtype=np.float64)
    if z.size < 200:
        return None
    xy = np.array([(v.co.x, v.co.y) for v in mesh.verts], dtype=np.float64)
    span = (hi - lo) * 0.008
    best = None
    for step in range(11, 112):           # 5.5% ~ 55.5% 를 0.5% 간격으로
        level = lo + (hi - lo) * step / 200.0
        band = xy[np.abs(z - level) < span]
        if len(band) < 12:
            continue
        radius = float(np.hypot(*(band - band.mean(axis=0)).T).mean())
        if best is None or radius < best[1]:
            best = (level, radius)
    if best is None:
        return None
    print(f"[trim] 가장 가는 단면: z={best[0]:.4f} (아래 {(best[0] - lo) / (hi - lo):.0%}) "
          f"반지름 {best[1]:.4f}")
    return best[0]


def find_neck(mesh, luma, sat, lo, hi):
    """지어낸 옷이 어디서 끝나는지 찾아 자를 높이를 돌려준다.

    옷은 무채색(흰·회색)이고 모델 아래쪽에 몰려 있다. 피부는 아무리 창백해도 붉은
    기가 남아 채도가 0.2 를 넘으므로, 밝기와 채도를 함께 보면 둘이 갈린다.

    밝기만 보면 안 된다. 예전에는 「밝은 면」만 골라 놓고 얼굴의 밝은 면을 피하려고
    후보를 모델 중간 아래로 잘랐는데, 그러면 99.9백분위가 그 자른 자리에 눌어붙는다.
    피부가 밝아 중간 높이까지 후보가 깔리기 때문이다. 실측한 다섯 모델이 전부
    45~50% 에서 잘렸다 — 얼굴 한가운데다. MAX_FRACTION 상한은 값이 구조상 50% 를
    넘을 수 없어 한 번도 걸리지 않았다.
    """
    if luma is None or sat is None:
        return None
    centres = np.array([f.calc_center_median().z for f in mesh.faces], dtype=np.float32)
    # 이제는 채도가 피부를 걸러 주므로 후보 범위를 상한까지 열어 둔다. 상한이 비로소
    # 살아 있는 안전장치가 된다 — 여기 걸리면 로그에 남는다.
    ceiling = lo + (hi - lo) * MAX_FRACTION
    garment = centres[(luma > WHITE_MIN) & (sat < NEUTRAL_MAX) & (centres < ceiling)]
    if garment.size < 50:
        return None                       # 지어낸 옷이 없다. 자동 판단을 포기한다.
    # 「옷이 어디서 끝나는가」를 직접 잡는다. 칸을 나눠 급락 지점을 찾는 방식은 옷의
    # 아래쪽만 지워 칼라 띠가 남았다(head03: 0.33 에서 잘랐는데 목 아래 흰 띠가 남음).
    # 흩어진 오분류에 끌려가지 않게 최댓값 대신 높은 백분위를 쓴다.
    cut = float(np.percentile(garment, PERCENTILE))
    print(f"[trim] 옷 탐지: 밝고 무채색인 면 {garment.size}개 · "
          f"{PERCENTILE}백분위 {cut:.4f} (아래 {(cut - lo) / (hi - lo):.0%})")
    if cut > ceiling:
        print(f"[trim] 옷 탐지 결과 {cut:.4f} 가 상한 {ceiling:.4f} 을 넘어 상한을 쓴다")
        cut = ceiling
    return cut


def main():
    global HAIR_MAX, WHITE_MIN, GROW, PERCENTILE
    argv = sys.argv[sys.argv.index("--") + 1:]
    src, dst = argv[0], argv[1]
    options = argv[2:]

    def take(name, cast, current):
        if name not in options:
            return current, options
        i = options.index(name)
        return cast(options[i + 1]), options[:i] + options[i + 2:]

    HAIR_MAX, options = take("--hair-max", float, HAIR_MAX)
    WHITE_MIN, options = take("--white-min", float, WHITE_MIN)
    GROW, options = take("--grow", int, GROW)
    PERCENTILE, options = take("--pct", float, PERCENTILE)
    fill = "--no-fill" not in options
    single = "--single-sided" in options
    plain = [a for a in options if not a.startswith("--")]
    fraction = float(plain[0]) if plain else None

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=src)
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit("메시가 없습니다")

    # 로컬 좌표로 자르므로 변환을 먼저 적용해 로컬 = 월드 로 만든다.
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    lo = min(min(v.co.z for v in o.data.vertices) for o in meshes)
    hi = max(max(v.co.z for v in o.data.vertices) for o in meshes)

    for obj in meshes:
        pixels = base_color_pixels(obj)
        mesh = bmesh.new()
        mesh.from_mesh(obj.data)
        mesh.faces.ensure_lookup_table()
        uv_layer = mesh.loops.layers.uv.active
        before = len(mesh.faces)
        rgb = face_colour(mesh, uv_layer, pixels)
        luma, sat = luminance(rgb), saturation(rgb)

        cut = None if fraction is not None else find_neck(mesh, luma, sat, lo, hi)
        if cut is None:
            used = FALLBACK if fraction is None else fraction
            cut = lo + (hi - lo) * used
            print(f"[trim] 자동 탐지를 쓰지 않음 · 비율 {used:.2f}")

        # 색으로 고른 높이를 기하로 검산한다. 색 판정이 어긋나도 얼굴은 지킨다 —
        # 예전에 창백한 피부를 흰 옷으로 잘못 보고 다섯 모델을 전부 얼굴 한가운데서
        # 자른 적이 있다. 손으로 비율을 준 경우(fraction)는 사람의 판단을 존중한다.
        if fraction is None:
            limit = narrowest_ring(mesh, lo, hi)
            if limit is not None and cut > limit:
                print(f"[trim] 자를 높이 {cut:.4f} 가 가장 가는 단면 {limit:.4f} 보다 "
                      f"위여서 단면 쪽을 쓴다")
                cut = limit
        print(f"[trim] 높이 {lo:.4f}~{hi:.4f}  자르는 높이 {cut:.4f}  "
              f"(아래 {(cut - lo) / (hi - lo):.0%})")

        hair = hair_faces(mesh, luma, GROW)
        targets = [f for f in mesh.faces if f.index not in hair]
        verts = {v for f in targets for v in f.verts}
        edges = {e for f in targets for e in f.edges}
        if targets:
            # 머리카락이 아닌 면만 평면으로 쪼갠 뒤 아래를 버린다. 머리카락은 높이와
            # 무관하게 통째로 남는다.
            bmesh.ops.bisect_plane(
                mesh, geom=list(verts) + list(edges) + targets, dist=1e-6,
                plane_co=Vector((0, 0, cut)), plane_no=Vector((0, 0, 1)),
                clear_inner=True, clear_outer=False)

        if fill:
            # 자른 자리의 경계만 메운다. 눈·입 안쪽처럼 원래 열려 있던 곳까지 메우면
            # 얼굴을 가로지르는 면이 생긴다(첫 판에서 실제로 그랬다).
            span = (hi - lo) * 0.02
            border = [e for e in mesh.edges if e.is_boundary
                      and abs((e.verts[0].co.z + e.verts[1].co.z) / 2 - cut) < span]
            if border:
                bmesh.ops.holes_fill(mesh, edges=border, sides=0)
            print(f"[trim] 메운 경계 변 {len(border)}개")

        # 목 단면을 재서 파일에 남긴다. 머리 모델의 바닥은 목이 아니라 머리카락 끝이라,
        # 나중에 몸통에 붙일 때 바닥을 재면 머리카락 굵기를 목으로 착각한다
        # (실제로 그래서 머리가 0.61배로 줄어 몸통에 작게 얹혔다).
        skin = [v for f in mesh.faces if f.index not in hair for v in f.verts
                if abs(v.co.z - cut) < (hi - lo) * 0.005]
        if len(skin) >= 8:
            xy = np.array([[v.co.x, v.co.y] for v in skin], dtype=np.float64)
            centre = xy.mean(axis=0)
            radius = float(np.hypot(*(xy - centre).T).mean())
            obj["neck_z"] = float(cut)
            obj["neck_x"], obj["neck_y"] = float(centre[0]), float(centre[1])
            obj["neck_r"] = radius
            print(f"[trim] 목 단면 기록: z={cut:.4f} "
                  f"중심=({centre[0]:.4f}, {centre[1]:.4f}) 반지름={radius:.4f} "
                  f"(정점 {len(skin)})")
        else:
            print("[trim] 목 단면을 재지 못했습니다 - 합칠 때 직접 지정해야 합니다")

        mesh.to_mesh(obj.data)
        mesh.free()
        obj.data.update()
        zs = [v.co.z for v in obj.data.vertices]
        print(f"[trim] '{obj.name}' 면 {before} -> {len(obj.data.polygons)} · "
              f"남은 높이 {min(zs):.4f}~{max(zs):.4f}")

    # 열린 껍데기(머리카락)는 뒷면이 컬링되면 뚫려 보인다. 원본은 경계 변이 7,721개라
    # 단면 재질로 두면 유니티에서 머리 사이로 배경이 비친다.
    for material in bpy.data.materials:
        material.use_backface_culling = single
    print(f"[trim] 재질 {len(bpy.data.materials)}개 · doubleSided = {not single}")

    # export_extras 를 켜야 위에서 적어 둔 목 단면(neck_z/x/y/r)이 GLB 에 실린다.
    # 이걸 빼면 합치는 쪽에서 읽을 수 없다.
    bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB",
                              export_yup=True, export_apply=False,
                              export_extras=True)
    print("[done]", dst)


main()
