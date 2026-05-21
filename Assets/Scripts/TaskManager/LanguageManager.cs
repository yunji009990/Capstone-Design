using System;
using UnityEngine;

public enum Language { KR, PH }

public class LanguageManager : MonoBehaviour
{
    public static LanguageManager Instance { get; private set; }

    [SerializeField] private Language currentLanguage = Language.KR;

    public Language CurrentLanguage => currentLanguage;
    public event Action<Language> OnLanguageChanged;

    void Awake()
    {
        if (Instance != null && Instance != this)
        {
            Destroy(gameObject);
            return;
        }
        Instance = this;
        DontDestroyOnLoad(gameObject);
    }

    public void SetLanguage(Language lang)
    {
        if (currentLanguage == lang) return;
        currentLanguage = lang;
        OnLanguageChanged?.Invoke(lang);
    }
}
