// FBX 에 실려 온 클립 하나를 그냥 반복 재생한다.
//
// 바리스타처럼 배경에서 계속 같은 동작만 하는 인물에는 상태 기계가 필요 없다.
// 그런데 Unity 는 Animator 에 Animator Controller 가 붙어 있어야 클립을 틀어
// 주고, 상태가 하나뿐인 컨트롤러를 만드는 것도 결국 에셋 하나를 더 두는 일이다.
// Playables 로 클립을 Animator 에 직접 물리면 컨트롤러 없이 끝난다.
//
// 시간도 직접 돌린다. FBX 클립은 임포트 설정에서 Loop Time 을 켜 두지 않으면
// 한 번 재생하고 마지막 프레임에서 멈춘다. 임포트 설정에 의존하지 않으려고
// 그래프를 Manual 로 두고 매 프레임 시간을 감아 준다.

using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;

[RequireComponent(typeof(Animator))]
public class ClipLooper : MonoBehaviour
{
    [Tooltip("재생할 클립. FBX 를 펼쳐서 안에 있는 클립을 그대로 끌어다 놓는다.")]
    public AnimationClip clip;

    [Tooltip("재생 속도. 1 이 원본 속도.")]
    public float speed = 1f;

    [Tooltip("끝까지 가면 처음으로 되감는다. 끄면 마지막 프레임에서 멈춘다.")]
    public bool loop = true;

    [Tooltip("시작 지점(초). 여러 인물이 같은 클립을 쓸 때 서로 어긋나게 한다.")]
    public float startOffsetSec = 0f;

    Animator _animator;
    PlayableGraph _graph;
    AnimationClipPlayable _playable;
    double _time;

    void OnEnable()
    {
        _animator = GetComponent<Animator>();

        if (clip == null)
        {
            Debug.LogWarning($"[ClipLooper] {name}: clip 이 비어 있어 재생하지 않습니다.", this);
            return;
        }

        _graph = PlayableGraph.Create($"ClipLooper-{name}");
        // 시간을 우리가 감으므로 그래프가 스스로 진행하지 않게 한다.
        _graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);

        var output = AnimationPlayableOutput.Create(_graph, "Animation", _animator);
        _playable = AnimationClipPlayable.Create(_graph, clip);
        _playable.SetApplyFootIK(false);
        output.SetSourcePlayable(_playable);

        _time = startOffsetSec;
        _graph.Play();
        Sample();   // 첫 프레임부터 바인드 포즈가 아니라 클립 자세로 서 있게 한다
    }

    void Update()
    {
        if (!_graph.IsValid()) return;

        _time += Time.deltaTime * speed;
        Sample();
    }

    void Sample()
    {
        float len = clip.length;
        if (len > 0f)
        {
            if (loop)
            {
                _time %= len;
                if (_time < 0d) _time += len;   // speed 가 음수일 때
            }
            else
            {
                _time = System.Math.Min(_time, len);
            }
        }

        _playable.SetTime(_time);
        _graph.Evaluate();
    }

    void OnDisable()
    {
        if (_graph.IsValid()) _graph.Destroy();
    }
}
