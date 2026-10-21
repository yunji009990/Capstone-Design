using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static partial class Scene2SkyCafe
{
    const int VolumeX=128,VolumeY=96,VolumeZ=96;

    public static void ApplyCloudDepth()
    {
        var scene=Scene2GraphicsCapture.RequireScene();
        var state=FindState();if(state==null)throw new InvalidOperationException("하늘 카페를 먼저 적용하세요.");
        Backup(scene);
        string backupFolder=Path.Combine(Work,"depth_before_"+DateTime.Now.ToString("yyyyMMdd_HHmmss"));
        Directory.CreateDirectory(backupFolder);
        foreach(string name in new[]{"Sky.mat","CloudWisps.mat"})
            File.Copy(Folder+"/"+name,Path.Combine(backupFolder,name),false);
        CaptureViews("depth_before_"+DateTime.Now.ToString("HHmmss"));
        string before=ProtectedDigest(),glass=GlassDigest();
        string garden=LayoutDigest(state.originalObjects.Select(o=>o.target));
        // 자료 준비가 실패하면 기존 구름은 그대로 남긴다.
        var materials=PrepareVolumeMaterials();
        ConfigureDepthSky();
        foreach(var child in state.transform.Cast<Transform>().Where(t=>t.name=="창 높이를 감싸는 달빛 구름"||t.name=="부피가 있는 달빛 구름").ToArray())
            Undo.DestroyObjectImmediate(child.gameObject);
        var group=PlaceVolumeClouds(state.transform,materials);
        Undo.RegisterCreatedObjectUndo(group.gameObject,"구름 입체감 적용");
        SetTheme(state,true);
        AssetDatabase.SaveAssets();EditorSceneManager.MarkSceneDirty(scene);EditorSceneManager.SaveScene(scene);
        ViewCafe();Inspect();
        bool protectedMatch=before==ProtectedDigest(),glassMatch=glass==GlassDigest();
        bool gardenMatch=garden==LayoutDigest(state.originalObjects.Select(o=>o.target));
        WriteReport("cloud_depth_preservation",new{protectedMatch,glassMatch,gardenMatch});
        if(!protectedMatch||!glassMatch||!gardenMatch)throw new InvalidOperationException("구름 변경 중 기존 씬 보존 검사 실패.");
        InspectCloudDepth();
        Debug.Log("[SkyCafe] 밀도·그늘이 있는 볼륨 구름과 해상도에 독립적인 달·별을 적용했습니다.");
    }

    static void ConfigureDepthSky()
    {
        var sky=Material("Sky","AgainSpring/Sky Cafe Atmosphere");
        sky.SetColor("_Zenith",new Color(.008f,.017f,.051f));
        sky.SetColor("_Horizon",new Color(.035f,.075f,.17f));
        sky.SetColor("_NebulaColor",new Color(.12f,.055f,.22f));
        sky.SetColor("_MoonColor",new Color(1.4f,2.1f,3.3f));
        sky.SetVector("_MoonDirection",new Vector4(.15f,.23f,1,0));
        sky.SetFloat("_MoonRadius",.04f);sky.SetFloat("_Exposure",1);
        EditorUtility.SetDirty(sky);
    }
    static void BuildDepthClouds(Transform root)=>PlaceVolumeClouds(root,PrepareVolumeMaterials());

    static Material[] PrepareVolumeMaterials()
    {
        var noise=CloudNoise();var mats=new Material[3];
        for(int i=0;i<mats.Length;i++)
        {
            var mat=Material(i==2?"VolumeCloudSea":"VolumeCloud"+i,"AgainSpring/Sky Cafe Cloud Volume");
            mat.SetTexture("_Volume",CloudDensity(i));mat.SetTexture("_Noise",noise);
            mat.SetTexture("_DetailMap",AssetDatabase.LoadAssetAtPath<Texture2D>(Folder+"/MoonlitCloudWisp.png"));
            mat.SetFloat("_PaintDetail",i==2?0:.94f);mat.SetFloat("_PhotoExposure",1.65f);
            mat.SetFloat("_PhotoFlip",i==1?1:0);mat.SetFloat("_Motion",1);
            mat.SetColor("_LightColor",new Color(.42f,.66f,1.10f));
            mat.SetColor("_ShadeColor",new Color(.008f,.018f,.055f));
            mat.SetColor("_RimColor",new Color(.55f,.85f,1.35f));
            mat.SetFloat("_Density",i==2?.65f:.9f);mat.SetFloat("_Absorption",i==2?.3f:.65f);
            mat.SetFloat("_Erosion",.22f);mat.SetFloat("_DetailScale",.08f);mat.SetFloat("_Steps",i==2?32:56);
            EditorUtility.SetDirty(mat);mats[i]=mat;
        }
        return mats;
    }
    static Transform PlaceVolumeClouds(Transform root,Material[] materials)
    {
        var group=Child(root,"부피가 있는 달빛 구름");
        Vector3[] p={new Vector3(-13.5f,3.4f,20),new Vector3(14.5f,2.6f,22),new Vector3(-1.3f,-4.5f,30),
            new Vector3(-20,2.6f,-2),new Vector3(19,3,0),new Vector3(-14,3,-23),new Vector3(12,4,-24),new Vector3(-2,2,58)};
        Vector3[] sizes={new Vector3(19,14,15),new Vector3(19,14,16),new Vector3(23,11,15),new Vector3(16,12,22),
            new Vector3(17,12,20),new Vector3(19,14,16),new Vector3(20,14,16),new Vector3(40,18,24)};
        var cube=Resources.GetBuiltinResource<Mesh>("Cube.fbx");
        for(int i=0;i<p.Length;i++)
        {
            var go=MeshObject(group,"입체 구름 "+(i+1),cube,materials[i%2],p[i]);go.transform.localScale=sizes[i];
            var direction=p[i]-Center;direction.y=0;go.transform.rotation=Quaternion.LookRotation(direction,Vector3.up);
        }
        MeshObject(group,"섬 아래 입체 구름 바다",cube,materials[2],new Vector3(-1.4f,-13,-1.5f)).transform.localScale=new Vector3(100,18,100);
        return group;
    }

    static Texture3D CloudNoise()
    {
        string path=Folder+"/CloudDetailNoise_Billows.asset";var existing=AssetDatabase.LoadAssetAtPath<Texture3D>(path);
        if(existing!=null)return existing;
        const int n=64;var pixels=new Color32[n*n*n];var noise=new float[pixels.Length];
        for(int z=0;z<n;z++)for(int y=0;y<n;y++)for(int x=0;x<n;x++)
        {
            var p=new Vector3(x/(float)n,y/(float)n,z/(float)n);
            noise[(z*n+y)*n+x]=Worley(p*4,4)*.5f+Worley(p*8,8)*.3f+Worley(p*16,16)*.2f;
        }
        var light=new Vector3(.30f,.85f,.45f).normalized;
        for(int z=0;z<n;z++)for(int y=0;y<n;y++)for(int x=0;x<n;x++)
        {
            int k=(z*n+y)*n+x;
            float dx=noise[(z*n+y)*n+(x+1)%n]-noise[(z*n+y)*n+(x+n-1)%n];
            float dy=noise[(z*n+(y+1)%n)*n+x]-noise[(z*n+(y+n-1)%n)*n+x];
            float dz=noise[(((z+1)%n)*n+y)*n+x]-noise[(((z+n-1)%n)*n+y)*n+x];
            float illumination=Vector3.Dot(-new Vector3(dx,dy,dz).normalized,light)*.5f+.5f;
            pixels[k]=new Color32((byte)Mathf.RoundToInt(noise[k]*255),(byte)Mathf.RoundToInt(illumination*255),0,255);
        }
        var tex=new Texture3D(n,n,n,TextureFormat.RGBA32,false){name="CloudDetailNoise_Billows",wrapMode=TextureWrapMode.Repeat,filterMode=FilterMode.Trilinear};
        tex.SetPixels32(pixels);tex.Apply(false,false);AssetDatabase.CreateAsset(tex,path);return tex;
    }
    static float Noise3(Vector3 p)=>(Mathf.PerlinNoise(p.x,p.y)+Mathf.PerlinNoise(p.y+17,p.z)+Mathf.PerlinNoise(p.z+31,p.x+7))/3;
    static float Smooth(float a,float b,float x){x=Mathf.Clamp01((x-a)/(b-a));return x*x*(3-2*x);}
    static int Voxel(int x,int y,int z)=>(z*VolumeY+y)*VolumeX+x;
    static float HashCell(int x,int y,int z,uint seed)
    {
        unchecked{uint h=(uint)x*374761393u+(uint)y*668265263u+(uint)z*2246822519u+seed*3266489917u;h=(h^(h>>13))*1274126177u;return (h^(h>>16))/4294967295f;}
    }
    static float Worley(Vector3 p,int period)
    {
        int x=Mathf.FloorToInt(p.x),y=Mathf.FloorToInt(p.y),z=Mathf.FloorToInt(p.z);float best=10;
        for(int dz=-1;dz<=1;dz++)for(int dy=-1;dy<=1;dy++)for(int dx=-1;dx<=1;dx++)
        {
            int a=((x+dx)%period+period)%period,b=((y+dy)%period+period)%period,c=((z+dz)%period+period)%period;
            float qx=x+dx+HashCell(a,b,c,1)-p.x,qy=y+dy+HashCell(a,b,c,2)-p.y,qz=z+dz+HashCell(a,b,c,3)-p.z;
            best=Mathf.Min(best,qx*qx+qy*qy+qz*qz);
        }
        return 1-Mathf.Clamp01(Mathf.Sqrt(best));
    }

    static Texture3D CloudDensity(int variant)
    {
        string name=variant==2?"CloudSeaDensity_Billows":"CloudDensity"+variant+"_Billows",path=Folder+"/"+name+".asset";
        var existing=AssetDatabase.LoadAssetAtPath<Texture3D>(path);if(existing!=null)return existing;
        var rho=new float[VolumeX*VolumeY*VolumeZ];var random=new System.Random(140+variant*37);
        var centers=new Vector3[11];var radii=new Vector3[11];
        for(int i=0;i<8;i++)
        {
            centers[i]=new Vector3(.23f+i%4*.18f,.31f+(float)random.NextDouble()*.16f,.36f+i/4*.25f);
            radii[i]=new Vector3(.18f+(float)random.NextDouble()*.07f,.18f+(float)random.NextDouble()*.10f,.23f+(float)random.NextDouble()*.08f);
        }
        centers[8]=new Vector3(.48f,.66f,.51f);radii[8]=new Vector3(.24f,.29f,.26f);
        centers[9]=new Vector3(.72f,.59f,.44f);radii[9]=new Vector3(.18f,.25f,.25f);
        centers[10]=new Vector3(.27f,.55f,.36f);radii[10]=new Vector3(.19f,.20f,.24f);
        for(int z=0;z<VolumeZ;z++)for(int y=0;y<VolumeY;y++)for(int x=0;x<VolumeX;x++)
        {
            var p=new Vector3(x/(float)(VolumeX-1),y/(float)(VolumeY-1),z/(float)(VolumeZ-1));float field=-10;
            if(variant==2)
            {
                float height=.46f+Mathf.PerlinNoise(p.x*4+7,p.z*4)*.14f+Mathf.PerlinNoise(p.x*13,p.z*13+12)*.07f;
                field=(height-p.y)*4;
            }
            else for(int i=0;i<centers.Length;i++)
            {
                Vector3 delta=p-centers[i],r=radii[i];
                float q=1-Mathf.Sqrt(delta.x*delta.x/(r.x*r.x)+delta.y*delta.y/(r.y*r.y)+delta.z*delta.z/(r.z*r.z));
                float h=Mathf.Max(.18f-Mathf.Abs(q-field),0)/.18f;field=Mathf.Max(field,q)+h*h*.045f;
            }
            field+=(Noise3(p*10+Vector3.one*(variant*17+4))-.5f)*1.0f;
            field+=(Noise3(p*28+Vector3.one*(variant*13+7))-.5f)*.45f;
            float edge=Mathf.Min(Mathf.Min(p.x,1-p.x),Mathf.Min(Mathf.Min(p.y,1-p.y),Mathf.Min(p.z,1-p.z)));
            rho[Voxel(x,y,z)]=Smooth(-.04f,.12f,field)*Smooth(0,.035f,edge);
        }
        var pixels=new Color32[rho.Length];var light=new Vector3(.30f,.85f,.45f).normalized;
        for(int z=0;z<VolumeZ;z++)for(int y=0;y<VolumeY;y++)for(int x=0;x<VolumeX;x++)
        {
            int k=Voxel(x,y,z);float value=rho[k];if(value<.001f){pixels[k]=new Color32(0,255,128,255);continue;}
            Vector3 p=new Vector3(x/(float)(VolumeX-1),y/(float)(VolumeY-1),z/(float)(VolumeZ-1));float optical=0;
            for(int s=1;s<=8;s++)optical+=ReadDensity(rho,p+light*(s*.032f))*.26f;
            float gx=rho[Voxel(Mathf.Min(x+1,VolumeX-1),y,z)]-rho[Voxel(Mathf.Max(x-1,0),y,z)];
            float gy=rho[Voxel(x,Mathf.Min(y+1,VolumeY-1),z)]-rho[Voxel(x,Mathf.Max(y-1,0),z)];
            float gz=rho[Voxel(x,y,Mathf.Min(z+1,VolumeZ-1))]-rho[Voxel(x,y,Mathf.Max(z-1,0))];
            Vector3 outward=-new Vector3(gx*VolumeX,gy*VolumeY,gz*VolumeZ).normalized;
            pixels[k]=new Color32((byte)Mathf.RoundToInt(value*255),(byte)Mathf.RoundToInt(Mathf.Exp(-optical*1.4f)*255),(byte)Mathf.RoundToInt((Vector3.Dot(outward,light)*.5f+.5f)*255),255);
        }
        var tex=new Texture3D(VolumeX,VolumeY,VolumeZ,TextureFormat.RGBA32,false){name=name,wrapMode=TextureWrapMode.Clamp,filterMode=FilterMode.Trilinear};
        tex.SetPixels32(pixels);tex.Apply(false,false);AssetDatabase.CreateAsset(tex,path);return tex;
    }
    static float ReadDensity(float[] data,Vector3 p)
    {
        if(p.x<0||p.x>1||p.y<0||p.y>1||p.z<0||p.z>1)return 0;
        float px=p.x*(VolumeX-1),py=p.y*(VolumeY-1),pz=p.z*(VolumeZ-1);int x=(int)px,y=(int)py,z=(int)pz;
        int x1=Mathf.Min(x+1,VolumeX-1),y1=Mathf.Min(y+1,VolumeY-1),z1=Mathf.Min(z+1,VolumeZ-1);float a=px-x,b=py-y,c=pz-z;
        return Mathf.Lerp(Mathf.Lerp(Mathf.Lerp(data[Voxel(x,y,z)],data[Voxel(x1,y,z)],a),Mathf.Lerp(data[Voxel(x,y1,z)],data[Voxel(x1,y1,z)],a),b),
            Mathf.Lerp(Mathf.Lerp(data[Voxel(x,y,z1)],data[Voxel(x1,y,z1)],a),Mathf.Lerp(data[Voxel(x,y1,z1)],data[Voxel(x1,y1,z1)],a),b),c);
    }

    public static void InspectCloudDepth()
    {
        var state=FindState();if(state==null)throw new InvalidOperationException("하늘 카페가 없습니다.");
        var clouds=state.GetComponentsInChildren<MeshRenderer>(true).Where(r=>IsVolumeCloud(r.sharedMaterial)).ToArray();
        var info=clouds.Select(r=>new{r.name,center=new[]{r.bounds.center.x,r.bounds.center.y,r.bounds.center.z},size=new[]{r.bounds.size.x,r.bounds.size.y,r.bounds.size.z},
            density=r.sharedMaterial.GetTexture("_Volume") is Texture3D,noise=r.sharedMaterial.GetTexture("_Noise") is Texture3D,steps=r.sharedMaterial.GetFloat("_Steps")}).ToArray();
        int cards=state.GetComponentsInChildren<Renderer>(true).Count(r=>r.sharedMaterials.Any(m=>m!=null&&m.shader.name=="AgainSpring/Sky Cafe Cloud Wisp"));
        bool valid=clouds.Length==9&&info.All(i=>i.density&&i.noise&&i.size.All(s=>s>5))&&cards==0;
        WriteReport("cloud_depth_validation",new{valid,cloudVolumes=info,flatCloudCards=cards,skyUsesPanorama=RenderSettings.skybox.HasProperty("_MainTex"),
            note="Editor 기하·자료 연결 검사. 실제 HMD 성능이나 GPU 프레임 검사가 아님."});
        if(!valid)throw new InvalidOperationException("입체 구름 연결 검사 실패.");
    }

    public static void CaptureCloudMotion()
    {
        var scene=Scene2GraphicsCapture.RequireScene();
        var all=scene.GetRootGameObjects().SelectMany(r=>r.GetComponentsInChildren<Renderer>(true)).ToArray();
        var states=all.Select(r=>r.forceRenderingOff).ToArray();
        var cloudMaterials=all.SelectMany(r=>r.sharedMaterials).Where(IsVolumeCloud).Distinct().ToArray();
        var motion=cloudMaterials.Select(m=>m.GetFloat("_Motion")).ToArray();
        try
        {
            foreach(var renderer in all)
                if(!renderer.sharedMaterials.Any(IsVolumeCloud))renderer.forceRenderingOff=true;
            foreach(var material in cloudMaterials)material.SetFloat("_Motion",0);
            var p=new Vector3(-.3f,1.55f,.2f);var turn=new Vector3(5,0,0);
            Capture("depth_control_a",p,turn);Capture("depth_control_b",p,turn);
            Capture("depth_eye_left",p+Vector3.left*.032f,turn);
            Capture("depth_eye_right",p+Vector3.right*.032f,turn);
            Capture("depth_head_shift",p+Vector3.left*.5f,turn);
        }
        finally
        {
            for(int i=0;i<all.Length;i++)if(all[i]!=null)all[i].forceRenderingOff=states[i];
            for(int i=0;i<cloudMaterials.Length;i++)cloudMaterials[i].SetFloat("_Motion",motion[i]);
            SceneView.RepaintAll();
        }
    }

    static bool IsVolumeCloud(Material material) => material != null &&
        (material.shader.name == "AgainSpring/Sky Cafe Cloud Volume" || material.shader.name == "AgainSpring/Sky Cafe Pastel Cloud Volume");
}
