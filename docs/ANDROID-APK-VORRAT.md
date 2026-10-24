# Lokaler APK-Vorrat für AVD

Native Android-Apps werden aus `data/android-apks` installiert. Ein AVD-Start lädt keine Apps aus dem Internet oder aus Google Play herunter. Bereits installierte Apps und ihre Daten bleiben erhalten; fehlende Apps werden einmal aus den lokalen Dateien installiert. Ein Update bestehender Apps ist ein gesonderter Schritt.

Aktuell hinterlegt:

- WEB.DE Mail, Cloud & News 9.18.3, Paket `de.web.mobile.android.mail`, Basis-APK und drei Zusatz-APKs für ARM64, Sprache und Bildschirmdichte.
- TikTok 47.1.3, Paket `com.zhiliaoapp.musically`, vorhandene APK aus dem Downloads-Ordner.

Die Pakete wurden vor dem Einlagern auf Paketnamen, einheitliche Versionskennung und das veröffentlichte Signaturzertifikat geprüft. Der lokale Index enthält SHA-256-Dateiprüfsummen. Vor einer Installation werden diese Dateien erneut geprüft. Die kryptografische Prüfung der APK-Signaturen übernimmt anschließend der Android-Paketinstaller. Ein übereinstimmendes Zertifikat ist keine Aussage über sämtliche Funktionen einer App.

Quellen und Zertifikatreferenzen:

- [WEB.DE-Download bei APKPure](https://apkpure.net/web-de-mail-cloud/de.web.mobile.android.mail/download)
- [WEB.DE-Zertifikatreferenz bei APKMirror](https://www.apkmirror.com/apk/web-de/web-de-mail-cloud/web-de-mail-cloud-9-17-3-release/web-de-mail-cloud-9-17-3-android-apk-download/)
- [TikTok-Zertifikatreferenz bei APKMirror](https://www.apkmirror.com/apk/tiktok-pte-ltd/tik-tok-including-musical-ly/tiktok-47-0-3-release/tiktok-47-0-3-4-android-apk-download/)

Die App-Installation ist unabhängig von einer Kontoerstellung. Ein installiertes Paket bestätigt keine Registrierung, keine Anmeldung und keinen erfolgreichen Mailabruf.
