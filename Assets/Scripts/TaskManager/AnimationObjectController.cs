using UnityEngine;
using UnityEngine.SceneManagement;
using System.Collections.Generic;

[System.Serializable]
public class AnimationObjectMapping
{
    public AnimationClip[] animationClips;      // 이 매핑이 반응할 애니메이션 클립들
    public GameObject[] objectsToActivate;      // 해당 클립일 때 켜야 할 오브젝트들
}

public class AnimationObjectController : MonoBehaviour
{
    public Animator animator;
    public AnimationObjectMapping[] animationMappings;

    private List<GameObject> currentlyActiveObjects = new List<GameObject>();
    private bool isSceneLoaded = false;

    private AnimationClip lastClip = null;      // 마지막으로 재생된 클립 저장용


    void Start()
    {
        SceneManager.sceneLoaded += OnSceneLoaded;

        // 씬이 이미 로드돼있으면 바로 실행
        if (SceneManager.GetActiveScene().isLoaded)
        {
            OnSceneLoaded(SceneManager.GetActiveScene(), LoadSceneMode.Single);
        }
    }


    void OnSceneLoaded(Scene scene, LoadSceneMode mode)
    {
        isSceneLoaded = true;

        // 시작 시점에는 모든 매핑 오브젝트를 비활성화
        foreach (var mapping in animationMappings)
        {
            DeactivateObjects(mapping.objectsToActivate);
        }
    }


    void Update()
    {
        if (!isSceneLoaded || animator == null) return;

        AnimatorClipInfo[] clipInfo = animator.GetCurrentAnimatorClipInfo(0);
        if (clipInfo.Length == 0) return;

        AnimationClip currentClip = clipInfo[0].clip;

        // ★★★ 핵심 수정 ★★★
        // 클립이 바뀌었을 때만 활성화/비활성화 로직 실행
        if (currentClip != lastClip)
        {
            if (GetFirstAnimationClip(lastClip) == GetFirstAnimationClip(currentClip)) return; // 동일 매핑 내 클립 전환 시 무시

            // 이전 활성 오브젝트들 비활성화
            DeactivateObjects(currentlyActiveObjects.ToArray());
            currentlyActiveObjects.Clear();

            // 현재 클립과 매칭되는 매핑을 찾아서 활성화
            foreach (var mapping in animationMappings)
            {
                foreach (var clip in mapping.animationClips)
                {
                    if (clip == currentClip)
                    {
                        ActivateObjects(mapping.objectsToActivate);
                        currentlyActiveObjects.AddRange(mapping.objectsToActivate);
                        break;
                    }
                }
            }

            lastClip = currentClip;  // 마지막 클립 갱신
        }
    }

    public AnimationClip GetFirstAnimationClip(AnimationClip currentClip)
    {
        foreach (var mapping in animationMappings)
        {
            foreach (var clip in mapping.animationClips)
            {
                if (clip == currentClip)
                {
                    return mapping.animationClips[0];
                }
            }
        }
        return null;
    }


    public GameObject[] GetActiveObjectsForClipName(string clipName)
    {
        foreach (var mapping in animationMappings)
        {
            foreach (var clip in mapping.animationClips)
            {
                if (clip != null && clip.name == clipName)
                {
                    return mapping.objectsToActivate;
                }
            }
        }
        return null;
    }


    private void ActivateObjects(GameObject[] objects)
    {
        foreach (GameObject obj in objects)
        {
            if (obj != null && !obj.activeSelf)
                obj.SetActive(true);
        }
    }


    private void DeactivateObjects(GameObject[] objects)
    {
        foreach (GameObject obj in objects)
        {
            if (obj != null && obj.activeSelf)
                obj.SetActive(false);
        }
    }


    void OnDestroy()
    {
        SceneManager.sceneLoaded -= OnSceneLoaded;
    }
}
