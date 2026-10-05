"""
【別実験】ゲーム単位のElo検証（アプリの入力単位に合わせた版）
 
tune_and_test.py（試合の勝敗を予測）とは別のモデルとして扱う。結果は混ぜない。
  tune_and_test.py     : 試合の勝敗を予測 → 試合単位のLog Loss
  このスクリプト       : 次の1ゲームの勝敗を予測 → ゲーム単位のLog Loss
 
流れ（1ゲームごと）
  1. その時点の4人のEloから、そのゲームのチーム1勝率を予測
  2. 実際のゲーム勝者を正解ラベルにする
  3. そのゲーム1つの点差からKを計算（点差割合 = |a-b| / (a+b)。アプリの入力と同じ定義）
  4. Eloを更新
  5. 次のゲームへ
 
手順
  - 2022〜23年のLog Lossだけで候補を比べる（調整）
  - 2024年以降で1回だけ評価する。結果を見てパラメータを変えない
  - 事前に決めた採用ルール: 調整期間で最良との差が PLATEAU_TOL 以内なら、現行の40・4を維持する
 
情報の入り方（試合単位との違い）
  - 同じ試合の2ゲーム目は、1ゲーム目の結果を知った状態で予測される（アプリでは自然な流れ）
  - 別の試合の結果は、同日なら漏らさない（その日の予測は、その日の開始時点のレートで行う）
  - 同じ試合の中でだけ、ゲームを順番に進める
 
比較する設定
  app_old : 元のアプリの設計（記録ゲーム数でK=40→24→16 + 点差/10を加算）
  current : 現在のstats.py（固定K=40、倍率の係数4）
  no_margin: 点差なしで、Kだけ調整期間で最適化
  selected: 調整期間で最良だった設定
"""
 
import csv
import random
import re
from collections import defaultdict
from datetime import date
from itertools import groupby, product
from math import log
from pathlib import Path
 
 
# ===== 設定 =====
DATA_FILE = Path(__file__).with_name("md.csv")
TUNE_START = date(2022, 1, 1)
TEST_START = date(2024, 1, 1)
INITIAL_ELO = 1500
SCALE = 400
 
NORMALIZE_NAMES = True
DROP_DUPLICATES = True
 
K_GRID = [16, 24, 32, 40, 50, 60, 80]
COEF_GRID = [0, 1, 2, 4, 6, 8]
CURRENT = {"kind": "fixed", "k": 40, "coef": 4}   # 現在のstats.py
APP_OLD = {"kind": "app_old"}
PLATEAU_TOL = 0.0005   # 調整期間で最良とのLog Loss差がこれ以内なら「ほぼ同等」とみなす
 
N_BOOT = 2000
SEED = 0
 
 
# ===== データ読み込み =====
def normalize_name(name):
    name = re.sub(r"\s*\[\d+\]", "", name).lower()
    return " ".join(sorted(re.findall(r"[^\W\d_]+", name)))
 
 
def split_team(value):
    players = [p.strip() for p in value.split("/")]
    if len(players) != 2 or not all(players):
        raise ValueError(value)
    if NORMALIZE_NAMES:
        players = [normalize_name(p) for p in players]
    return players
 
 
def is_complete_game(a, b):
    """途中棄権などの未完了ゲームを除く（21点先取・2点差・30点上限のルールで完了したゲームのみ）"""
    hi, lo = max(a, b), min(a, b)
    if hi == 30:
        return lo in (28, 29)
    if hi == 21:
        return lo <= 19
    return 22 <= hi <= 29 and hi - lo == 2
 
 
def parse_games(score):
    games = [(int(a), int(b)) for a, b in re.findall(r"(\d+)-(\d+)", score or "")]
    return [(a, b) for a, b in games if is_complete_game(a, b)], len(games)
 
 
def load_matches():
    with DATA_FILE.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
 
    matches, seen = [], set()
    stats = defaultdict(int)
    for row in rows:
        try:
            match_date = date.fromisoformat(row["date"])
            team1 = split_team(row["team1"])
            team2 = split_team(row["team2"])
            winner = int(row["winner"])
        except (ValueError, KeyError):
            continue
        if winner not in (1, 2) or len(set(team1 + team2)) != 4:
            continue
 
        if DROP_DUPLICATES:
            key = (match_date, row.get("tournament", ""),
                   tuple(sorted(team1)), tuple(sorted(team2)), winner)
            if key in seen:
                continue
            seen.add(key)
 
        games, raw_count = parse_games(row.get("score", ""))
        stats["games_raw"] += raw_count
        stats["games_incomplete_dropped"] += raw_count - len(games)
 
        # 完了ゲームの勝ち数から決まる勝者が、winner列と食い違う試合は向きが怪しいので除く
        w1 = sum(a > b for a, b in games)
        w2 = sum(b > a for a, b in games)
        if (w1 > w2 and winner != 1) or (w2 > w1 and winner != 2):
            stats["matches_conflict_dropped"] += 1
            continue
        if not games:
            stats["matches_no_games"] += 1
            continue
 
        matches.append({
            "date": match_date,
            "team1": team1,
            "team2": team2,
            "games": games,
            "cluster": f"{row.get('tournament', '')}|{match_date.year}",
        })
 
    matches.sort(key=lambda m: m["date"])
    return matches, stats
 
 
# ===== Elo（1ゲーム単位） =====
def win_probability(r1, r2):
    return 1 / (1 + 10 ** ((r2 - r1) / SCALE))
 
 
def schedule_k(games):
    if games < 10:
        return 40
    if games < 30:
        return 24
    return 16
 
 
def play_game(rating, played, team1, team2, a, b, cfg):
    """1ゲームを処理して、(更新前の勝率, 結果) を返す。rating / played はその場で更新される"""
    r1 = sum(rating[p] for p in team1) / 2
    r2 = sum(rating[p] for p in team2) / 2
    expected = win_probability(r1, r2)
    y = 1 if a > b else 0
 
    if cfg["kind"] == "app_old":
        g1 = sum(played[p] for p in team1) / 2
        g2 = sum(played[p] for p in team2) / 2
        k = (schedule_k(g1) + schedule_k(g2)) / 2 + abs(a - b) / 10
    else:
        k = cfg["k"] * (1 + cfg["coef"] * abs(a - b) / (a + b))
 
    delta = k * (y - expected)
    for p in team1:
        rating[p] += delta
        played[p] += 1
    for p in team2:
        rating[p] -= delta
        played[p] += 1
    return expected, y
 
 
def run_model(matches, cfg):
    """TUNE_START 以降の各ゲームについて (日付, クラスタ, 予測確率, 結果, 試合内のゲーム番号) を返す"""
    rating = defaultdict(lambda: float(INITIAL_ELO))
    played = defaultdict(int)
    out = []
 
    for match_date, group in groupby(matches, key=lambda m: m["date"]):
        day = list(group)
 
        # 予測：その日の開始時点のレートから、同じ試合の中だけゲームを順番に進める
        if match_date >= TUNE_START:
            for m in day:
                members = m["team1"] + m["team2"]
                local_rating = {p: rating[p] for p in members}
                local_played = {p: played[p] for p in members}
                for n, (a, b) in enumerate(m["games"], start=1):
                    expected, y = play_game(local_rating, local_played,
                                            m["team1"], m["team2"], a, b, cfg)
                    out.append((match_date, m["cluster"], expected, y, n))
 
        # 予測のあとで、実際のレートを更新
        for m in day:
            for a, b in m["games"]:
                play_game(rating, played, m["team1"], m["team2"], a, b, cfg)
 
    return out
 
 
# ===== 評価指標 =====
EPS = 1e-15
 
 
def point_loss(p, y):
    p = min(max(p, EPS), 1 - EPS)
    return -(y * log(p) + (1 - y) * log(1 - p))
 
 
def in_period(out, start, end=None):
    return [r for r in out if r[0] >= start and (end is None or r[0] < end)]
 
 
def metrics(rows):
    n = len(rows)
    return {
        "n": n,
        "log_loss": sum(point_loss(r[2], r[3]) for r in rows) / n,
        "brier": sum((r[2] - r[3]) ** 2 for r in rows) / n,
        "accuracy": sum((r[2] >= 0.5) == (r[3] == 1) for r in rows) / n,
    }
 
 
def paired_bootstrap(rows_a, rows_b):
    """大会単位（大会名+年）で再サンプリングし、b - a の差の95%信頼区間を返す"""
    per_cluster = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
    for ra, rb in zip(rows_a, rows_b):
        c = per_cluster[ra[1]]
        c[0] += point_loss(rb[2], rb[3]) - point_loss(ra[2], ra[3])
        c[1] += (rb[2] - rb[3]) ** 2 - (ra[2] - ra[3]) ** 2
        c[2] += ((rb[2] >= 0.5) == (rb[3] == 1)) - ((ra[2] >= 0.5) == (ra[3] == 1))
        c[3] += 1
 
    clusters = list(per_cluster.values())
    total_n = sum(c[3] for c in clusters)
    point = [sum(c[i] for c in clusters) / total_n for i in range(3)]
 
    rng = random.Random(SEED)
    samples = [[], [], []]
    for _ in range(N_BOOT):
        picked = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        n = sum(c[3] for c in picked)
        for i in range(3):
            samples[i].append(sum(c[i] for c in picked) / n)
 
    result = []
    for i in range(3):
        s = sorted(samples[i])
        result.append((point[i], s[int(N_BOOT * 0.025)], s[int(N_BOOT * 0.975) - 1]))
    return result
 
 
def describe(cfg):
    if cfg["kind"] == "app_old":
        return "元のアプリ設計（記録ゲーム数でK変化 + 点差/10加算）"
    if cfg["coef"] == 0:
        return f"固定K={cfg['k']}・点差なし"
    return f"固定K={cfg['k']}・点差倍率 係数{cfg['coef']}"
 
 
def same(c1, c2):
    return c1 == c2
 
 
# ===== メイン =====
def main():
    matches, stats = load_matches()
    n_games = sum(len(m["games"]) for m in matches)
    print(f"使用した試合: {len(matches):,} / ゲーム: {n_games:,}")
    print(f"除外: 未完了ゲーム {stats['games_incomplete_dropped']:,}件, "
          f"勝敗とスコアが食い違う試合 {stats['matches_conflict_dropped']:,}件, "
          f"完了ゲームなしの試合 {stats['matches_no_games']:,}件")
    print(f"調整期間: {TUNE_START} 〜 {TEST_START}（前日まで） / 評価期間: {TEST_START} 以降")
    print("※ 予測するのは『次の1ゲームの勝敗』。試合単位の結果とは混ぜないこと")
    print()
 
    # --- 1. 調整期間で候補を比較 ---
    grid = [{"kind": "fixed", "k": k, "coef": c} for k, c in product(K_GRID, COEF_GRID)]
    tuned = []
    for cfg in grid + [APP_OLD]:
        out = run_model(matches, cfg)
        tuned.append((metrics(in_period(out, TUNE_START, TEST_START))["log_loss"], cfg))
    tune_n = metrics(in_period(out, TUNE_START, TEST_START))["n"]
 
    ranked = sorted([t for t in tuned if t[1]["kind"] == "fixed"], key=lambda t: t[0])
    best_ll = ranked[0][0]
    print(f"【調整期間】{tune_n:,}ゲーム・{len(grid)}通りを比較（Log Loss、小さいほど良い）")
    for ll, cfg in ranked[:8]:
        print(f"  {ll:.4f}  {describe(cfg)}")
    app_old_ll = next(ll for ll, cfg in tuned if cfg["kind"] == "app_old")
    current_ll = next(ll for ll, cfg in tuned if same(cfg, CURRENT))
    print(f"  --- 参考 ---")
    print(f"  {app_old_ll:.4f}  {describe(APP_OLD)}")
    print(f"  {current_ll:.4f}  現行 {describe(CURRENT)}")
    print()
 
    plateau = [(ll, cfg) for ll, cfg in ranked if ll - best_ll <= PLATEAU_TOL]
    print(f"最良とのLog Loss差が{PLATEAU_TOL}以内の「ほぼ同等」グループ: {len(plateau)}件 / {len(grid)}件")
    keep_current = same(CURRENT, CURRENT) and (current_ll - best_ll) <= PLATEAU_TOL
    print(f"現行(40・4)と最良の差: {current_ll - best_ll:+.4f}")
    print("事前ルールの判定:",
          "現行の40・4はほぼ同等グループ内 → 40・4を維持" if keep_current
          else "現行の40・4はほぼ同等グループの外 → 変更を検討")
    print()
 
    selected = ranked[0][1]
    no_margin = min((t for t in ranked if t[1]["coef"] == 0), key=lambda t: t[0])[1]
 
    # --- 2. 評価期間で1回だけ評価 ---
    configs = {"app_old": APP_OLD, "no_margin": no_margin, "current": CURRENT, "selected": selected}
    test = {name: in_period(run_model(matches, cfg), TEST_START) for name, cfg in configs.items()}
 
    print(f"【評価期間】{len(test['current']):,}ゲーム（ここで1回だけ評価。結果を見てパラメータを変えない）")
    labels = {"app_old": "元のアプリ設計", "no_margin": "点差なし最良", "current": "現行40・4", "selected": "調整期間の最良"}
    for name in ["app_old", "no_margin", "current", "selected"]:
        m = metrics(test[name])
        print(f"  {labels[name]:10s} 的中率 {m['accuracy']:.2%}  Log Loss {m['log_loss']:.4f}  "
              f"Brier {m['brier']:.4f}   [{describe(configs[name])}]")
    print()
 
    # --- 3. 差の不確かさ ---
    print(f"【差の95%信頼区間】大会単位ブートストラップ {N_BOOT}回（マイナスのLog Loss/Brierは改善）")
    pairs = [("app_old", "current", "元のアプリ設計 → 現行40・4"),
             ("app_old", "no_margin", "元のアプリ設計 → 点差なし最良（Kの調整だけ）"),
             ("no_margin", "current", "点差なし最良 → 現行40・4（点差倍率だけ）"),
             ("current", "selected", "現行40・4 → 調整期間の最良（変更する価値があるか）"),
             ("app_old", "selected", "元のアプリ設計 → 調整期間の最良"),
             ("no_margin", "selected", "点差なし最良 → 調整期間の最良（点差倍率を足した効果）")]
    for a, b, label in pairs:
        if same(configs[a], configs[b]):
            continue
        ll, br, acc = paired_bootstrap(test[a], test[b])
        print(f"  {label}")
        print(f"    Log Loss {ll[0]:+.4f} [{ll[1]:+.4f}, {ll[2]:+.4f}]  "
              f"Brier {br[0]:+.4f} [{br[1]:+.4f}, {br[2]:+.4f}]  "
              f"的中率 {acc[0]*100:+.2f}pt [{acc[1]*100:+.2f}, {acc[2]*100:+.2f}]")
    print()
 
    # --- 4. 試合内のゲーム番号別（情報の入り方の違いを見る） ---
    print("【ゲーム番号別のLog Loss】2ゲーム目以降は、同じ試合の前のゲーム結果を知った状態で予測している")
    for name in ["app_old", "current"]:
        first = [r for r in test[name] if r[4] == 1]
        later = [r for r in test[name] if r[4] >= 2]
        print(f"  {labels[name]:10s} 1ゲーム目 {metrics(first)['log_loss']:.4f}（{len(first):,}） / "
              f"2ゲーム目以降 {metrics(later)['log_loss']:.4f}（{len(later):,}）")
    print()
 
    with DATA_FILE.with_name("tuning_results_game.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tune_log_loss", "k", "coef"])
        for ll, cfg in ranked:
            w.writerow([f"{ll:.5f}", cfg["k"], cfg["coef"]])
    print("保存: tuning_results_game.csv（調整期間の全候補）")
 
 
if __name__ == "__main__":
    main()