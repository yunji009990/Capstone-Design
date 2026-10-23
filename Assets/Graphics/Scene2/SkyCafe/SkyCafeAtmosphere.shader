Shader "AgainSpring/Sky Cafe Atmosphere"
{
    Properties
    {
        _Zenith ("밤하늘", Color) = (.008,.017,.051,1)
        _Horizon ("수평선의 푸른 빛", Color) = (.035,.075,.17,1)
        _NebulaColor ("은은한 보랏빛", Color) = (.12,.055,.22,1)
        _MoonDirection ("달의 방향", Vector) = (.15,.23,1,0)
        _MoonRadius ("달 크기", Range(.01,.1)) = .04
        [HDR] _MoonColor ("달빛", Color) = (1.4,2.1,3.3,1)
        _Exposure ("하늘 밝기", Range(.2,2)) = 1
    }
    SubShader
    {
        Tags { "RenderPipeline"="UniversalPipeline" "Queue"="Background" "RenderType"="Background" "PreviewType"="Skybox" }
        Cull Off ZWrite Off
        Pass
        {
            HLSLPROGRAM
            #pragma target 3.5
            #pragma vertex Vert
            #pragma fragment Frag
            #pragma multi_compile_instancing
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            CBUFFER_START(UnityPerMaterial)
                half4 _Zenith, _Horizon, _NebulaColor, _MoonColor;
                float4 _MoonDirection;
                float _MoonRadius, _Exposure;
            CBUFFER_END
            struct A { float4 positionOS:POSITION; UNITY_VERTEX_INPUT_INSTANCE_ID };
            struct V { float4 positionCS:SV_POSITION; float3 direction:TEXCOORD0; UNITY_VERTEX_OUTPUT_STEREO };
            V Vert(A i)
            {
                V o; UNITY_SETUP_INSTANCE_ID(i); UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                o.positionCS=TransformObjectToHClip(i.positionOS.xyz);
                o.direction=TransformObjectToWorldDir(i.positionOS.xyz); return o;
            }
            float Hash(float3 p) { p=frac(p*.1031); p+=dot(p,p.yzx+33.33); return frac((p.x+p.y)*p.z); }
            float Noise(float3 p)
            {
                float3 a=floor(p),f=frac(p);f=f*f*(3-2*f);
                return lerp(lerp(lerp(Hash(a),Hash(a+float3(1,0,0)),f.x),lerp(Hash(a+float3(0,1,0)),Hash(a+float3(1,1,0)),f.x),f.y),
                    lerp(lerp(Hash(a+float3(0,0,1)),Hash(a+float3(1,0,1)),f.x),lerp(Hash(a+float3(0,1,1)),Hash(a+1),f.x),f.y),f.z);
            }
            float Fbm(float3 p) {return Noise(p)*.55+Noise(p*2.05+17)*.28+Noise(p*4.15+31)*.12+Noise(p*8.3)*.05;}
            half Stars(float3 d,float density)
            {
                float2 uv=float2(atan2(d.x,d.z)*.159154943+.5,asin(clamp(d.y,-1,1))*.318309886+.5);
                float2 grid=uv*float2(density,density*.5), cell=floor(grid);
                float seed=Hash(float3(cell,19));
                float2 center=.18+.64*float2(Hash(float3(cell,7)),Hash(float3(cell,23)));
                float radius=.045+Hash(float3(cell,51))*.075;
                float2 offset=frac(grid)-center;
                float pixel=max(length(fwidth(grid)),.001);
                float width=radius*radius+pixel*pixel*.10;
                float starSpot=exp(-dot(offset,offset)/width)*min(1,radius*radius/(pixel*pixel*.12));
                return starSpot*step(.988,seed)*(.35+seed*.7);
            }
            half4 Frag(V i):SV_Target
            {
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(i);
                float3 d=normalize(i.direction);
                half3 sky=lerp(_Horizon.rgb,_Zenith.rgb,pow(saturate(d.y+.02),.45));
                float field=Fbm(d*3.7+float3(8,4,13));
                float ribbon=exp(-pow((d.x*.7+d.y*.5-d.z*.3+.05)*2.1,2));
                sky+=_NebulaColor.rgb*smoothstep(.43,.79,field)*ribbon*.5;
                sky+=half3(.025,.055,.10)*smoothstep(.58,.85,Fbm(d*9.7+12))*.28;
                half stars=(Stars(d,780)*.9+Stars(d,320)*.36)*smoothstep(-.10,.16,d.y);
                sky+=stars*half3(.60,.76,1.1);
                float3 moon=normalize(_MoonDirection.xyz);
                float3 right=normalize(cross(float3(0,1,0),moon)),up=cross(moon,right);
                float2 p=float2(dot(d,right),dot(d,up))/_MoonRadius;
                float r=length(p), aa=max(fwidth(r),.002);
                float disc=1-smoothstep(1-aa,1+aa,r);
                float cut=1-smoothstep(.91-aa,.91+aa,length(p-float2(.36,.14)));
                float crescent=disc*(1-cut)*step(.9,dot(d,moon));
                sky+=_MoonColor.rgb*crescent;
                sky+=half3(.035,.065,.15)*exp(-r*r*.13)*step(.7,dot(d,moon));
                return half4(sky*_Exposure,1);
            }
            ENDHLSL
        }
    }
}
