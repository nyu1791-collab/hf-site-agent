# Commander reliability and cost-safety

## 目的

ChatGPT Workを最高司令部に固定し、QwenとDeepSeekを同格の上級司令Agentとして並列に使う。両者は提案・分解・レビューだけを行い、実装、デプロイ、公開、投稿、決済、秘密値変更は司令部の承認なしに実行しない。

## 今回確認した原因

- OpenRouterの無料モデルIDは入れ替わるため、固定したQwen/DeepSeekのIDがカタログから消えるとHTTP 404になる。
- 旧実装は一方の司令Agentが失敗すると、成功したもう一方の結果まで捨ててしまう。
- 外部モデルの空応答、非JSON、命令権限を越える出力を十分に検査していなかった。
- 一時的な429/5xx/タイムアウトへの再試行、試行回数、費用上限が明示されていなかった。
- preflightの出力改行が壊れると、Actionsの後続stepがモデル選定結果を誤読する可能性があった。

## 対策

1. Read-only preflightでOpenRouter公開カタログだけを確認する。
2. 指定IDを優先し、役割ごとの系統（Planner=Qwen、Critic=DeepSeek）を固定する。消えていれば同じ系統かつ価格0・free suffixの候補だけを決定的に選ぶ。paid、generic fallback、別系統への置換はしない。
3. 候補がなければモデル呼び出しを0回でblockedにする。402や課金フォールバックも停止する。
4. Qwen/DeepSeekを同時に最大2回まで呼び、タイムアウト・接続・429・5xxだけを1回再試行する。
5. JSON形式、司令Agent必須フィールド、execution_allowed=false、requires_commander_approval=trueを通常コードで検証する。
6. Fan-inは部分成功を保持する。一方が失敗しても、成功側の提案と成果物を司令部へ返す。
7. handoff packetにstatus、attempts、provider_status、error_code、model_calls、execution_allowedを記録し、秘密値・詳細レスポンスをログへ出さない。
8. 追加の下位作業は構造化されたread-only指示書として返し、司令部が個別承認したものだけを次工程へ進める。

## 現在の指揮系統

ChatGPT Work（最高司令部）
  - Qwen commander（Planner）
  - DeepSeek commander（Critic）
  - 下位specialist/workerへの指示案
  - ChatGPT WorkがFan-in、採用、実行許可を決定

QwenとDeepSeekは兄弟Agentであり、互いに命令しない。上方向への昇格、Peer-to-peerの委譲、無制限spawnは許可しない。

## 完了条件

- preflightが無料・同系統モデルだけを選定する。
- 選定不能時は有料呼び出しなしでblockedになる。
- 片側失敗時も成功側の結果が失われない。
- 不正JSONや実行許可要求が昇格しない。
- Python compile、JSON schema、既存runtime tests、新しいdelegation testsが成功する。
- Durable Object、既存binding、secret、YouTube、決済、外部公開は変更しない。

## 未対応課題

OpenRouterの公開カタログにQwen系とDeepSeek系の価格0モデルが同時に存在しない場合、実運用の2体会議は安全にblockedになる。モデルを有料へ自動変更することはせず、司令部がカタログと費用を確認してから再試行する。

実際の需要検証では、成功率だけでなく、採用率、再利用率、処理時間、試行回数、cache hit率、部分失敗率を蓄積してから改善案を採用する。