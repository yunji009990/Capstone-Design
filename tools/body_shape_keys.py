"""기존 연결 몸체에 체형 Shape Key를 추가한다. 정점·본·웨이트 구조는 유지한다.

Blender Python에서 add_body_shape_keys(body, rig)를 호출한다.
전체 키·팔다리 길이·관절 위치는 변경하지 않는 체형 실루엣 조절이다.
"""
import hashlib
import json

import bpy
import numpy as np


CONTROLS = (
    ("상체_폭", "가슴·등의 좌우 폭", .18),
    ("상체_두께", "상체의 앞뒤 두께", .24),
    ("허리_폭", "허리와 옆구리의 폭", .16),
    ("배_볼륨", "배 앞쪽의 볼륨", .045),
    ("팔_굵기", "양팔 소매의 굵기", .24),
    ("다리_굵기", "양쪽 바지의 굵기", .20),
)
PRESETS = {
    "표준": (0, 0, 0, 0, 0, 0),
    "마름": (-.65, -.5, -.6, -.45, -.35, -.35),
    "체격": (.55, .65, .6, .75, .45, .4),
}


def smooth(lo, hi, value):
    t = np.clip((value-lo)/(hi-lo), 0, 1)
    return t*t*(3-2*t)


def band(value, start, full_start, full_end, end):
    return smooth(start, full_start, value)*(1-smooth(full_end, end, value))


def fingerprint(body, rig):
    vertices = np.array([v.co[:] for v in body.data.vertices], dtype=np.float64)
    faces = [tuple(p.vertices) for p in body.data.polygons]
    weights = [[(body.vertex_groups[g.group].name, g.weight) for g in v.groups] for v in body.data.vertices]
    bones = [(b.name, b.parent.name if b.parent else None,
              [list(row) for row in b.matrix_local], b.use_deform) for b in rig.data.bones]
    uv = [[tuple(x.uv) for x in layer.data] for layer in body.data.uv_layers]
    def sha(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
    return {"vertices": sha(vertices.tolist()), "faces": sha(faces), "weights": sha(weights),
            "bones": sha(bones), "uv": sha(uv)}


def closest_segment(points, start, end):
    direction = end-start
    t = np.clip((points-start)@direction/(direction@direction), 0, 1)
    center = start + t[:, None]*direction
    return center, np.linalg.norm(points-center, axis=1)


def add_body_shape_keys(body, rig):
    if body.type != 'MESH' or rig.type != 'ARMATURE':
        raise ValueError('연결 몸체 메시와 Armature가 필요합니다')
    if not any(m.type == 'ARMATURE' and m.object == rig for m in body.modifiers):
        raise ValueError('몸체가 지정 리그에 연결돼 있지 않습니다')
    if body.data.shape_keys is not None:
        raise ValueError('기존 Shape Key를 덮어쓰지 않습니다')
    before = fingerprint(body, rig)
    points = np.array([tuple(body.matrix_world@v.co) for v in body.data.vertices])
    z = points[:, 2];x = points[:, 0]
    def joint(name):
        return np.array(tuple(rig.matrix_world@rig.data.bones[name].head_local))
    hip = joint('root.x'); chest = joint('spine_03.x')
    center_y = hip[1] + (chest[1]-hip[1])*np.clip((z-hip[2])/(chest[2]-hip[2]), 0, 1)
    middle = 1-smooth(.25, .34, np.abs(x))
    protected = set()
    for poly in body.data.polygons:
        if body.data.materials[poly.material_index].name.startswith(('Natural_Hand_', 'Cuff_Inner_')):
            protected.update(poly.vertices)
    locked = np.zeros(len(points), dtype=bool)
    locked[list(protected)] = True
    # 목/옷깃은 머리 결합 기준이다. 양손과 검은 소매 안감도 고정한다.
    locked |= ((z >= 1.735) & (np.abs(x) < .15)) | (z < .215)
    masks = {
        'chest': band(z,1.24,1.38,1.59,1.72)*middle,
        'torso': band(z,1.10,1.25,1.58,1.72)*middle,
        'waist': band(z,1.05,1.16,1.31,1.45)*middle,
        'belly': band(z,1.14,1.24,1.36,1.51)*(1-smooth(.12,.24,np.abs(x))),
    }
    deltas = [np.zeros_like(points) for _ in CONTROLS]
    deltas[0][:,0] = x*.18*masks['chest']
    deltas[1][:,1] = (points[:,1]-center_y)*.24*masks['torso']
    deltas[2][:,0] = x*.16*masks['waist']
    forward = (joint('toes_01.l')-joint('foot.l')) + (joint('toes_01.r')-joint('foot.r'))
    forward[2] = 0;forward /= np.linalg.norm(forward)
    centers = np.c_[np.zeros(len(points)),center_y,z]
    front = smooth(.015,.08,(points-centers)@forward)
    deltas[3] = forward[None,:] * (.045*masks['belly']*front)[:,None]
    weights = np.zeros((len(points),len(body.vertex_groups)))
    for v in body.data.vertices:
        for group in v.groups:weights[v.index,group.group] = group.weight
    for side in ('l','r'):
        for key_index, names, controls in (
            (4, ('arm_stretch','forearm_stretch','hand'), ('arm_stretch','arm_twist','forearm_stretch','forearm_twist')),
            (5, ('thigh_stretch','leg_stretch','foot'), ('thigh_stretch','thigh_twist','leg_stretch','leg_twist')),
        ):
            a,b,c = [joint(name+'.'+side) for name in names]
            first,fd = closest_segment(points,a,b);second,sd = closest_segment(points,b,c)
            center = np.where((fd<sd)[:,None],first,second)
            groups = [body.vertex_groups.get(name+'.'+side) for name in controls]
            influence = np.clip(sum((weights[:,g.index] for g in groups if g is not None),np.zeros(len(points))),0,1)
            if key_index == 4:
                fade = smooth(.045,.14,np.linalg.norm(points-a,axis=1))
                fade *= smooth(.07,.17,np.linalg.norm(points-c,axis=1))
                amount = .24
            else:
                fade = smooth(.075,.18,np.linalg.norm(points-a,axis=1))
                fade *= smooth(.085,.20,np.linalg.norm(points-c,axis=1))
                fade *= smooth(.025,.065,np.abs(x))
                amount = .20
            deltas[key_index] += (points-center) * (amount*influence*fade)[:,None]
    body.shape_key_add(name='Basis', from_mix=False)
    body.data.shape_keys.use_relative = True
    inverse = np.array(body.matrix_world.inverted().to_3x3())
    report = []
    base = np.array([v.co[:] for v in body.data.vertices])
    for (name,description,_),delta in zip(CONTROLS,deltas):
        delta[locked] = 0
        key = body.shape_key_add(name=name, from_mix=False)
        key.slider_min = -1;key.slider_max = 1;key.value = 0
        key.data.foreach_set('co', (base+delta@inverse.T).astype(np.float32).reshape(-1))
        report.append({'name':name,'changedVertices':int((np.linalg.norm(delta,axis=1)>1e-7).sum()),
                       'maximumDeltaMeters':float(np.linalg.norm(delta,axis=1).max())})
    body['체형_조절_안내'] = 'Shape Keys 값 -1=가늘게, 0=기본, 1=굵게. 키·뼈 길이는 유지.'
    body['체형_항목_설명'] = json.dumps({name:description for name,description,_ in CONTROLS},ensure_ascii=False)
    body['체형_프리셋'] = json.dumps(PRESETS,ensure_ascii=False)
    after = fingerprint(body,rig)
    assert before == after, '기본 메시·본·웨이트·UV가 바뀌었습니다'
    return {'before':before,'after':after,'preserved':before==after,'controls':report,
            'protectedVertices':int(locked.sum()),'bones':len(rig.data.bones),'vertices':len(points)}


def set_preset(body, name):
    for (key,_,_),value in zip(CONTROLS, PRESETS[name]):
        body.data.shape_keys.key_blocks[key].value = value
    bpy.context.view_layer.update()
