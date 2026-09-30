# 作業開始と動画制作の復元

このリポジトリの作業は現在の `ai-army/provider-v3` と PR #40 の状態を確認してから行う。
チャット履歴、過去の報告、手元に残る旧ファイルより現在の GitHub の変更を優先する。
最新の明示的ユーザー指示が制作上の好みを変更した場合は、現行設定も更新する。

起動時は README → `config/current_commander_handoff.json` →
`config/permanent_standards_manifest.json` → `docs/AI_ARMY_MASTER_RULEBOOK.md` を読む。
動画の制作・修正・短縮・型の改善を意味する依頼では、必ず
`config/current_media_quality_handoff.json`、`config/approved_video_template.json`、
`docs/VIDEO_PRODUCTION_BASELINE.md`、`docs/VOICEVOX_RUNTIME.md` を読み、`config/media_command_read_gate.json`
から該当する追加ルールを復元する。単語の一致だけで判断しない。

`scripts/restore_video_context.py` が参照一覧・内容ハッシュ・復元パックを出す。
復元パックは実際に読む。保存済みというだけで復元完了にしない。
欠落・矛盾・HEAD変更は該当ファイルを読み直して解消してから制作する。

承認された約47秒動画が現行の見た目と演技の基準。
淡い字幕、拡大した公式メディア引用、両キャラのネイティブ表情と音声同期の口、
無音・聞き手の閉口、音声・素材の再利用を維持する。
旧静止キャラ動画と濃い字幕の設定へ戻さない。
重要な過去音声・資料は `config/media_reference_history.json` にあるが現行指示ではない。

PR は draft/open のまま。main push、merge、deploy、public publish、秘密情報変更、
無許可の有料メディア生成をこの制作承認から許可したと解釈しない。
