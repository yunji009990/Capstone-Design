"""기존 메시를 읽지 않고 성인 남성 베이스를 새 기하로 제작한다.

Blender --background --python tools/create_male_base.py -- --out <새 .blend>
표면 제작용 스크립트다. 기존 Human/참여자 모델/외부 텍스처는 사용하지 않는다.
"""
import argparse
import json
import math
from pathlib import Path
import sys

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
TAU = math.tau
FINGER_SPECS = []


def enum_value(owner, name, value):
    available = {item.identifier for item in owner.bl_rna.properties[name].enum_items}
    if value not in available:
        raise RuntimeError(f"{name}: {value} not in {available}")
    setattr(owner, name, value)


def material(name, color, roughness=.6):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    node = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    node.inputs['Base Color'].default_value = (*color, 1)
    node.inputs['Roughness'].default_value = roughness
    mat.diffuse_color = (*color, 1)
    return mat


def mesh_object(name, verts, faces, collection, mat=None):
    mesh = bpy.data.meshes.new(name + '_Surface')
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    if bm.calc_volume(signed=True) < 0:
        bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    if mat:
        mesh.materials.append(mat)
    for poly in mesh.polygons:
        poly.use_smooth = True
    return obj


def interpolate(rows, samples=4):
    """해부학적 단면 위치를 잇는 연속적인 Catmull-Rom 로프트."""
    rows = np.asarray(rows, dtype=float)
    result = []
    for i in range(len(rows) - 1):
        a, b = rows[max(i-1, 0)], rows[i]
        c, d = rows[i+1], rows[min(i+2, len(rows)-1)]
        for j in range(samples):
            t = j / samples
            result.append(.5*((2*b)+(-a+c)*t+(2*a-5*b+4*c-d)*t*t+(-a+3*b-3*c+d)*t*t*t))
    result.append(rows[-1])
    return result


def loft(name, rows, axis, collection, segments=40, samples=4, sculpt=None):
    """각 행은 중심 x/y/z와 단면 반경2개. 끝면이 닫힌 새 메시만 만든다."""
    verts, faces = [], []
    rows = interpolate(rows, samples)
    for row in rows:
        x, y, z, a, b = row
        for k in range(segments):
            angle = k * TAU / segments
            u, v = math.cos(angle), math.sin(angle)
            a1, b1 = max(a, .0005), max(b, .0005)
            if axis == 'Z': point = Vector((x+a1*u, y+b1*v, z))
            elif axis == 'X': point = Vector((x, y+a1*u, z+b1*v))
            else: point = Vector((x+a1*u, y, z+b1*v))
            if sculpt:
                point = sculpt(point, angle, row)
            verts.append(tuple(point))
    for j in range(len(rows)-1):
        for k in range(segments):
            a = j*segments+k
            b = j*segments+(k+1)%segments
            faces.append((a,b,b+segments,a+segments))
    faces.append(tuple(reversed(range(segments))))
    faces.append(tuple((len(rows)-1)*segments+k for k in range(segments)))
    return mesh_object(name, verts, faces, collection)


def ellipsoid(name, center, radii, collection, segments=32, rings=20):
    verts = [(center[0], center[1], center[2]-radii[2])]
    for j in range(1, rings):
        phi = math.pi*j/rings
        for k in range(segments):
            theta = TAU*k/segments
            verts.append((center[0]+radii[0]*math.sin(phi)*math.cos(theta),
                          center[1]+radii[1]*math.sin(phi)*math.sin(theta),
                          center[2]-radii[2]*math.cos(phi)))
    top = len(verts)
    verts.append((center[0], center[1], center[2]+radii[2]))
    faces=[]
    for k in range(segments): faces.append((0,1+(k+1)%segments,1+k))
    for j in range(rings-2):
        for k in range(segments):
            a=1+j*segments+k; b=1+j*segments+(k+1)%segments
            faces.append((a,b,b+segments,a+segments))
    for k in range(segments): faces.append((top,top-segments+k,top-segments+(k+1)%segments))
    return mesh_object(name,verts,faces,collection)


def union(name, objects, collection, voxel, mat, smoothing=4):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects: obj.select_set(True)
    bpy.context.view_layer.objects.active=objects[0]
    bpy.ops.object.join()
    obj=objects[0]; obj.name=name
    remesh=obj.modifiers.new('Connected anatomical surface','REMESH')
    enum_value(remesh,'mode','VOXEL')
    remesh.voxel_size=voxel
    remesh.use_smooth_shade=True
    bpy.ops.object.modifier_apply(modifier=remesh.name)
    if smoothing:
        smooth=obj.modifiers.new('Surface relaxation','SMOOTH')
        smooth.factor=.62; smooth.iterations=smoothing
        bpy.ops.object.modifier_apply(modifier=smooth.name)
    obj.data.materials.clear(); obj.data.materials.append(mat)
    for poly in obj.data.polygons: poly.use_smooth=True
    obj['origin']='Original procedural geometry; no existing mesh, rig or texture imported.'
    return obj


def torso_sculpt(p, angle, row):
    x,y,z=p
    front=max(0,-math.sin(angle))**5
    back=max(0,math.sin(angle))**4
    # 가슴·복부·쇄골·등의 양감. 기본 비율을 유지하고 근육 표현은 작게 더한다.
    pec=math.exp(-((abs(x)-.080)/.065)**2-((z-1.355)/.047)**2)*.012
    sternum=math.exp(-(x/.018)**2-((z-1.35)/.09)**2)*.003
    abdomen=math.exp(-((abs(x)-.039)/.038)**2-((z-1.195)/.10)**2)*.003
    clavicle=math.exp(-((z-(1.44-.10*abs(x)))/.009)**2)*.003
    y-=front*(pec-sternum+abdomen+clavicle)
    y+=back*math.exp(-((abs(x)-.08)/.065)**2-((z-1.37)/.065)**2)*.008
    return Vector((x,y,z))


def make_body(collection, clay, nail_mat):
    parts=[]
    sections=[
        (.835,.000,.047,.057),(.87,.008,.106,.083),(.92,.014,.145,.105),
        (.98,.014,.164,.112),(1.035,.008,.163,.105),(1.09,.003,.147,.093),
        (1.15,.000,.142,.090),(1.21,.000,.153,.096),(1.27,.003,.172,.103),
        (1.33,.005,.187,.109),(1.38,.008,.191,.107),(1.42,.012,.183,.091),
        (1.455,.015,.145,.076),(1.482,.018,.089,.057),(1.51,.017,.053,.045),
        (1.55,.015,.046,.043),(1.575,.010,.045,.042)]
    rows=[(0,y,z,xr,yr) for z,y,xr,yr in sections]
    parts.append(loft('Torso_new_geometry',rows,'Z',collection,64,5,torso_sculpt))
    for side in [-1,1]:
        # 어깨·위팔·팔꿈치·아래팔·손목. 안쪽 끝을 몸통에 완전히 묻어 평평한 뚜껑이 드러나지 않게 한다.
        arm=[(.085,.012,1.433,.036,.025),(.13,.012,1.434,.052,.044),(.17,.011,1.435,.068,.061),(.20,.009,1.435,.075,.072),
             (.245,.006,1.435,.065,.067),(.29,.001,1.433,.056,.060),
             (.35,.000,1.432,.050,.053),(.405,.000,1.432,.042,.043),
             (.447,.001,1.433,.036,.035),(.485,.001,1.433,.039,.039),
             (.535,.002,1.434,.042,.040),(.59,.002,1.435,.034,.032),
             (.645,.001,1.435,.027,.025),(.69,.000,1.435,.026,.021),
             (.716,.000,1.435,.027,.020)]
        parts.append(loft('Arm_from_sections',[(side*x,y,z,a,b) for x,y,z,a,b in arm],'X',collection,40,5))
        palm=[(.687,.000,1.435,.025,.020),(.72,-.001,1.435,.032,.020),
              (.75,.003,1.435,.041,.020),(.772,.005,1.435,.043,.016),
              (.784,.007,1.435,.036,.012)]
        parts.append(loft('Palm_from_sections',[(side*x,y,z,a,b) for x,y,z,a,b in palm],'X',collection,40,4))
        finger_data=[('Index',.769,-.029,.086,.0093,-.003),
                     ('Middle',.777,-.004,.096,.0099,0),
                     ('Ring',.771,.021,.089,.0093,.002),
                     ('Little',.758,.043,.073,.0079,.005)]
        for fname,base,cy,length,radius,splay in finger_data:
            path=[]
            for t,bulge in [(0,1.05),(.16,1),(.36,.94),(.44,1.02),(.52,.90),(.69,.82),(.75,.90),(.83,.76),(.94,.68),(.995,.15)]:
                path.append((side*(base+length*t),cy+splay*t,1.434,radius*bulge,radius*.83*bulge))
            parts.append(loft(fname+'_finger_new',path,'X',collection,24,2))
            FINGER_SPECS.append((side,fname,base,cy,length,radius,splay))
        # 엄지의 시작은 손목보다 앞쪽이다. 무지구에서 말절까지 방향을 자연스럽게 잇는다.
        parts.append(ellipsoid('Thenar_new',(side*.741,-.030,1.428),(.037,.024,.020),collection))
        thumb=[(.738,-.032,1.429,.016,.014),(.752,-.046,1.429,.014,.0125),
               (.773,-.061,1.429,.012,.011),(.787,-.070,1.429,.011,.010),
               (.802,-.077,1.429,.009,.008),(.809,-.079,1.429,.003,.003)]
        parts.append(loft('Thumb_new',[(side*x,y,z,a,b) for x,y,z,a,b in thumb],'X',collection,28,3))
        leg=[(.110,.018,.073,.030,.038),(.111,.018,.12,.031,.041),
             (.112,.023,.19,.038,.047),(.113,.020,.29,.054,.060),
             (.113,.018,.35,.058,.061),(.113,.012,.42,.047,.052),
             (.112,-.001,.487,.040,.048),(.110,-.004,.525,.045,.051),
             (.107,.000,.59,.058,.065),(.104,.006,.68,.072,.079),
             (.099,.012,.78,.084,.089),(.092,.016,.87,.088,.092),
             (.085,.015,.945,.073,.084),(.079,.015,.982,.052,.063)]
        parts.append(loft('Leg_new',[(side*x,y,z,a,b) for x,y,z,a,b in leg],'Z',collection,48,5))
        parts.append(ellipsoid('Gluteal_volume_new',(side*.073,.073,.945),(.081,.063,.099),collection))
        # 무릎뼈를 앞쪽에 작게 겹친다. 종아리 뒤쪽은 로프트의 양감으로 표현한다.
        parts.append(ellipsoid('Patella_new',(side*.112,-.036,.491),(.031,.016,.037),collection))
        foot=[(.11,.072,.066,.026,.045),(.11,.045,.061,.034,.052),
              (.11,.005,.056,.038,.049),(.11,-.045,.052,.041,.046),
              (.11,-.095,.046,.049,.038),(.11,-.140,.039,.051,.029),
              (.11,-.176,.032,.046,.022)]
        parts.append(loft('Foot_new',[(side*x,y,z,a,b) for x,y,z,a,b in foot],'Y',collection,40,4))
        toes=[(-.030,-.193,.0145,.034),(-.006,-.200,.0113,.033),(.015,-.194,.0102,.029),(.033,-.184,.0091,.025),(.047,-.172,.0078,.021)]
        for offset,cy,width,length in toes:
            parts.append(ellipsoid('Toe_new',(side*(.11+offset),cy,.029),(width,length,.020),collection,24,14))
    body=union('Male_Body_New',parts,collection,.0027,clay,4)
    # 각 부위를 합친 경계만 넓게 이완한다. 손가락·발가락의 굵기는 유지한다.
    joint_group=body.vertex_groups.new(name='Construction_JointBlend')
    for vertex in body.data.vertices:
        x,y,z=vertex.co
        shoulder=math.exp(-((abs(x)-.155)/.10)**2-((z-1.435)/.095)**2)
        hip=math.exp(-((abs(x)-.09)/.12)**2-((z-.94)/.12)**2)
        weight=max(shoulder,hip)
        if weight > .005: joint_group.add([vertex.index],weight,'REPLACE')
    relaxed=body.modifiers.new('Shoulder and pelvis continuity','SMOOTH')
    relaxed.vertex_group=joint_group.name
    blend_group_name=joint_group.name
    relaxed.factor=.95; relaxed.iterations=85
    bpy.context.view_layer.objects.active=body
    bpy.ops.object.modifier_apply(modifier=relaxed.name)
    applied_group=body.vertex_groups.get(blend_group_name)
    if applied_group is not None: body.vertex_groups.remove(applied_group)
    # 발바닥의 접지만 정리한다. 기존 메시·골격에서 데이터를 옮기지 않는다.
    for vertex in body.data.vertices:
        if vertex.co.z < .012: vertex.co.z=.003+(vertex.co.z/.012)*.005
        if vertex.co.z < .15:
            t=max(0.0,min(1.0,(.15-vertex.co.z)/.09))
            weight=t*t*(3-2*t)
            vertex.co.y=.018+(vertex.co.y-.018)*(1-.12*weight)
    body.data.update()
    body['target_height_with_head_m']=1.78
    body['pose']='T pose, fingers extended, palms facing floor'
    body['body_role']='body'
    make_nails(collection,nail_mat,body)
    return body


def make_nails(collection, mat, body):
    verts=[]; faces=[]
    bpy.context.view_layer.update()
    surface=BVHTree.FromObject(body,bpy.context.evaluated_depsgraph_get())
    def on_skin(x,y):
        point,normal,_,_=surface.ray_cast(Vector((x,y,1.52)),Vector((0,0,-1)))
        if point is None: raise RuntimeError('Nail missed finger surface')
        return tuple(point+normal*.00025)
    for side,name,base,cy,length,radius,splay in FINGER_SPECS:
        start=len(verts)
        for i in range(7):
            u=i/6; t=.74+u*.20
            half=radius*.59*math.sin(math.pi*(.08+u*.84))**.3
            for j in range(7):
                v=j/3-1
                verts.append(on_skin(side*(base+length*t),cy+splay*t+v*half))
        for i in range(6):
            for j in range(6):
                a=start+i*7+j; faces.append((a,a+1,a+8,a+7))
    for side in [-1,1]:
        start=len(verts)
        for i in range(7):
            u=i/6; cx=.788+u*.015; cy=-.070-u*.007
            half=.0060*math.sin(math.pi*(.08+u*.84))**.3
            for j in range(7):
                v=j/3-1
                verts.append(on_skin(side*(cx+v*half*.42),cy+v*half*.91))
        for i in range(6):
            for j in range(6):
                a=start+i*7+j; faces.append((a,a+1,a+8,a+7))
    obj=mesh_object('Fingernails_New',verts,faces,collection,mat)
    obj['body_role']='nails'
    solid=obj.modifiers.new('Thin nail plate','SOLIDIFY'); solid.thickness=.0002
    return obj


def make_head(collection,clay):
    rows=[(0,.006,1.548,.026,.035),(0,.008,1.561,.045,.049),
          (0,.012,1.585,.061,.061),(0,.012,1.615,.072,.069),
          (0,.014,1.648,.077,.075),(0,.016,1.683,.075,.078),
          (0,.014,1.716,.073,.078),(0,.012,1.746,.065,.069),
          (0,.012,1.769,.043,.050),(0,.012,1.78,.003,.004)]
    def sculpt(p,angle,row):
        front=max(0,-math.sin(angle))**8
        # 안와·광대의 기준 형태만 만든다. 특정 인물의 얼굴 자료를 사용하지 않는다.
        socket=math.exp(-((abs(p.x)-.031)/.018)**2-((p.z-1.672)/.013)**2)*.006
        brow=math.exp(-((p.z-1.692)/.008)**2-((abs(p.x)-.032)/.025)**2)*.004
        p.y+=front*(socket-brow)
        return p
    parts=[loft('Cranium_and_jaw_new',rows,'Z',collection,64,5,sculpt)]
    parts.append(ellipsoid('Nose_bridge_new',(0,-.063,1.663),(.010,.017,.028),collection))
    parts.append(ellipsoid('Nose_tip_new',(0,-.079,1.645),(.013,.020,.012),collection))
    for side in [-1,1]:
        parts.append(ellipsoid('Ear_new',(side*.077,.015,1.654),(.012,.017,.031),collection))
        parts.append(ellipsoid('Ear_lobe_new',(side*.077,.010,1.631),(.010,.012,.012),collection))
    head=union('Head_ProportionGuide_New',parts,collection,.0023,clay,3)
    head['body_role']='head_guide'
    head['note']='Generic original head guide. Replace with a generated head for the actual character.'
    return head


def marker(name, location, collection):
    obj=bpy.data.objects.new(name,None)
    collection.objects.link(obj)
    obj.location=location
    obj.empty_display_size=.025
    enum_value(obj,'empty_display_type','ARROWS')
    return obj


def studio(scene,collection):
    floor=material('Studio charcoal',(.055,.061,.070),.8)
    mesh_object('Studio_Floor',[(-3,-3,0),(3,-3,0),(3,3,0),(-3,3,0)],[(0,1,2,3)],collection,floor)
    for name,loc,energy,size in [('Key',(-2,-3,3.5),350,2.5),('Fill',(2,-1,2),140,2.0),('Rim',(0,2,3),450,2.0)]:
        data=bpy.data.lights.new(name,'AREA'); data.energy=energy; data.shape='DISK'; data.size=size
        obj=bpy.data.objects.new(name,data); collection.objects.link(obj); obj.location=loc
        obj.rotation_euler=(Vector((0,0,1))-obj.location).to_track_quat('-Z','Y').to_euler()
    data=bpy.data.cameras.new('Male Review Camera'); cam=bpy.data.objects.new('Male Review Camera',data)
    collection.objects.link(cam); cam.location=(2.7,-4.5,2.0)
    cam.rotation_euler=(Vector((0,0,.92))-cam.location).to_track_quat('-Z','Y').to_euler()
    enum_value(data,'type','ORTHO'); data.ortho_scale=2.2
    scene.camera=cam
    scene.world=bpy.data.worlds.new('Male Review World'); scene.world.use_nodes=True
    bg=next(n for n in scene.world.node_tree.nodes if n.type=='BACKGROUND')
    bg.inputs['Color'].default_value=(.12,.14,.18,1); bg.inputs['Strength'].default_value=.4
    try: scene.render.engine='BLENDER_EEVEE_NEXT'
    except TypeError: pass
    scene.render.resolution_x=1100; scene.render.resolution_y=1100; scene.render.resolution_percentage=100
    scene.view_settings.exposure=-.5
    return cam


def validate(body,head):
    result={}
    for obj in [body,head]:
        bm=bmesh.new(); bm.from_mesh(obj.data)
        seen=set(); components=[]
        for v in bm.verts:
            if v in seen: continue
            stack=[v]; seen.add(v); count=0
            while stack:
                a=stack.pop(); count+=1
                for edge in a.link_edges:
                    b=edge.other_vert(a)
                    if b not in seen: seen.add(b); stack.append(b)
            components.append(count)
        result[obj.name]={'vertices':len(bm.verts),'faces':len(bm.faces),
                          'non_manifold_edges':sum(not e.is_manifold for e in bm.edges),
                          'euler_characteristic':len(bm.verts)-len(bm.edges)+len(bm.faces),
                          'components':sorted(components,reverse=True),
                          'bounds_min':list(np.min(np.array([tuple(v.co) for v in bm.verts]),axis=0)),
                          'bounds_max':list(np.max(np.array([tuple(v.co) for v in bm.verts]),axis=0))}
        bm.free()
    tree=KDTree(len(body.data.vertices))
    for vertex in body.data.vertices: tree.insert(vertex.co,vertex.index)
    tree.balance()
    max_error=0.0
    for vertex in body.data.vertices:
        _,_,distance=tree.find(Vector((-vertex.co.x,vertex.co.y,vertex.co.z)))
        max_error=max(max_error,distance)
    result['body_mirror_max_error_m']=max_error
    result['checks']={'body_single_connected_surface':len(result[body.name]['components'])==1,
                      'body_manifold':result[body.name]['non_manifold_edges']==0,
                      'no_unintended_handles':result[body.name]['euler_characteristic']==2,
                      'head_manifold':result[head.name]['non_manifold_edges']==0,
                      'bilateral_symmetry_within_voxel':max_error<.004}
    if not all(result['checks'].values()): raise RuntimeError('New body geometry inspection failed: '+str(result['checks']))
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=ROOT/'tools/model_sources/Male_FromScratch.blend')
    parser.add_argument('--render',action='store_true',help='검토용 전체 모습 PNG도 저장')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene; scene.name='Male From Scratch Studio'
    enum_value(scene.unit_settings,'system','METRIC'); scene.unit_settings.scale_length=1
    collection=bpy.data.collections.new('Male_FromScratch'); scene.collection.children.link(collection)
    clay=material('Male neutral warm clay',(.32,.30,.28),.65)
    nails=material('Nail satin',(.40,.37,.34),.36)
    body=make_body(collection,clay,nails)
    head=make_head(collection,clay)
    ground=min(v.co.z for v in body.data.vertices)
    crown=max(v.co.z for v in head.data.vertices)
    scale=1.78/(crown-ground)
    for obj in collection.objects:
        if obj.type=='MESH':
            for vertex in obj.data.vertices:
                vertex.co=Vector((vertex.co.x*scale,vertex.co.y*scale,(vertex.co.z-ground)*scale))
            obj.data.update()
    marker('HeadSocket',(0,.010*scale,(1.555-ground)*scale),collection)
    marker('NeckSeam',(0,.010*scale,(1.535-ground)*scale),collection)
    studio_collection=bpy.data.collections.new('Review_Studio'); scene.collection.children.link(studio_collection)
    studio(scene,studio_collection)
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type=='VIEW_3D':
                region=area.spaces.active.region_3d
                region.view_location=(0,0,.9); region.view_distance=3.1
                region.view_rotation=Vector((0,-1,.08)).to_track_quat('Z','Y')
                enum_value(area.spaces.active.shading,'color_type','MATERIAL')
    bpy.ops.object.select_all(action='DESELECT'); body.select_set(True); bpy.context.view_layer.objects.active=body
    report={'provenance':'All mesh vertices generated in this script. No existing model, rig, UV or texture loaded.',
            'target_height_m':1.78,'stage':'New anatomical base geometry; runtime integration not performed.',
            'geometry':validate(body,head)}
    folder=ROOT/'tools/_work/male_from_scratch'; folder.mkdir(parents=True,exist_ok=True)
    (folder/'geometry.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.out.resolve()),compress=True)
    print(json.dumps(report,ensure_ascii=False),flush=True)
    if args.render:
        enum_value(scene.render.image_settings,'file_format','PNG')
        scene.render.filepath=str(folder/'male_overview.png')
        bpy.ops.render.render(write_still=True)


if __name__=='__main__': main()
