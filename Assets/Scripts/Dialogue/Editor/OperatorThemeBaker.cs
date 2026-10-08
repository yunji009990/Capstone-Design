// OperatorThemeBaker.cs
// OperatorTheme 가 실행 중에 입히던 모양을 씬에 영구히 적용한다.
//   1) 코드로 그린 스프라이트를 PNG 에셋으로 저장한다 (Assets/Scripts/Raon/Theme).
//   2) 열려 있는 씬의 운영자 화면에 같은 스타일을 입힌다.
// 씬은 저장하지 않고 "변경됨" 으로만 표시한다. 결과가 마음에 들면 Ctrl+S 로 저장한다.

using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class OperatorThemeBaker
{
    const string OutDir = "Assets/Scripts/Raon/Theme";

    [MenuItem("Tools/Raon/Bake Operator Theme into Scene")]
    static void Bake()
    {
        if (EditorApplication.isPlaying || EditorApplication.isPlayingOrWillChangePlaymode)
        {
            EditorUtility.DisplayDialog("운영자 테마", "Play 를 멈춘 뒤 실행하세요.", "확인");
            return;
        }

        var hud = Object.FindObjectOfType<OperatorHUD>(true);
        if (hud == null)
        {
            EditorUtility.DisplayDialog("운영자 테마", "열려 있는 씬에 OperatorHUD 가 없습니다. Scene_2 를 열고 다시 실행하세요.", "확인");
            return;
        }

        if (hud.transform.Find("Backdrop/StatusCard/_Face") != null &&
            !EditorUtility.DisplayDialog("운영자 테마", "이미 적용된 흔적이 있습니다. 한 번 더 적용할까요?", "적용", "취소"))
            return;

        Directory.CreateDirectory(OutDir);
        Undo.RegisterFullObjectHierarchyUndo(hud.gameObject, "Bake Operator Theme");

        OperatorTheme.ClearCache();
        OperatorTheme.SpriteSink = SaveSprite;
        try
        {
            OperatorTheme.Apply(hud.transform);
        }
        finally
        {
            OperatorTheme.SpriteSink = null;
            OperatorTheme.ClearCache();
        }

        EditorUtility.SetDirty(hud.gameObject);
        EditorSceneManager.MarkSceneDirty(hud.gameObject.scene);
        AssetDatabase.SaveAssets();
        Debug.Log("[OperatorTheme] 씬에 적용했습니다. 확인한 뒤 Ctrl+S 로 저장하세요. 되돌리려면 Ctrl+Z 또는 씬을 저장하지 않고 닫으세요.");
    }

    static Sprite SaveSprite(string key, Texture2D tex, int border)
    {
        string path = $"{OutDir}/OperatorTheme_{key}.png";
        File.WriteAllBytes(path, tex.EncodeToPNG());
        AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);

        var imp = (TextureImporter)AssetImporter.GetAtPath(path);
        imp.textureType = TextureImporterType.Sprite;
        imp.spriteImportMode = SpriteImportMode.Single;
        imp.spritePixelsPerUnit = 100f;
        imp.spriteBorder = new Vector4(border, border, border, border);
        imp.mipmapEnabled = false;
        imp.alphaIsTransparency = true;
        imp.filterMode = FilterMode.Bilinear;
        imp.wrapMode = TextureWrapMode.Clamp;
        imp.textureCompression = TextureImporterCompression.Uncompressed;
        imp.SaveAndReimport();

        return AssetDatabase.LoadAssetAtPath<Sprite>(path);
    }
}
