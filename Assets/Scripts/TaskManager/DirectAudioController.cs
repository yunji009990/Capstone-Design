using System.Collections.Generic;
using UnityEngine;

[RequireComponent(typeof(AudioSource))]
public class DirectAudioController : MonoBehaviour
{
    [Tooltip("언어별 클립 폴더 (Resources 하위 경로). {0}이 언어 코드로 치환됨.")]
    [SerializeField] private string localizedPathFormat = "AudioClips/Rec_Voice_{0}";

    private AudioSource audioSource;
    private readonly Dictionary<(Language, string), AudioClip> cache = new();

    void Awake()
    {
        audioSource = GetComponent<AudioSource>();
    }

    public void PlayClip(AudioClip clip)
    {
        if (clip == null || audioSource == null)
        {
            Debug.LogWarning("AudioSource 또는 AudioClip이 할당되지 않았습니다.");
            return;
        }

        if (LanguageManager.Instance == null)
        {
            Debug.LogError("[DirectAudioController] 씬에 LanguageManager가 없습니다. 빈 GameObject에 LanguageManager 컴포넌트를 추가하세요.");
            return;
        }

        Language lang = LanguageManager.Instance.CurrentLanguage;
        AudioClip localized = LoadLocalizedClip(lang, clip.name);
        if (localized == null)
        {
            string path = string.Format(localizedPathFormat, lang) + "/" + clip.name;
            Debug.LogError($"[DirectAudioController] '{lang}' 언어용 클립 '{clip.name}'을(를) 찾을 수 없습니다. 경로: Resources/{path}");
            return;
        }
        audioSource.PlayOneShot(localized);
    }

    private AudioClip LoadLocalizedClip(Language lang, string clipName)
    {
        var key = (lang, clipName);
        if (cache.TryGetValue(key, out var cached)) return cached;

        string path = string.Format(localizedPathFormat, lang) + "/" + clipName;
        var loaded = Resources.Load<AudioClip>(path);
        cache[key] = loaded;
        return loaded;
    }
}
