# 旧検証: 試合単位でEloの設定を比較した実験。
# 現在の最終設計の根拠には tune_and_test_game.py を使用する。

"""
個人Eloの設定を「調整」と「評価」に分けて検証するスクリプト
 
手順
  1. 調整期間（TUNE_START 〜 TEST_START の前日）のLog Lossだけを使って、
     K値・点差倍率の係数・点差の定義を選ぶ
  2. 選んだ設定を、評価期間（TEST_START 以降）で「1回だけ」評価する
  3. 大会単位のブートストラップで、差の95%信頼区間を出す
 
守ること
  - 評価期間の結果を見てから、候補やパラメータを変えない
    （変えたくなったら、それは評価期間が調整に使われたことになる）
  - 評価期間を新しい試合に更新したいときは、TEST_START を後ろにずらす
 
比較する3つの設定
  original : 元の設計（試合数でKを40→24→16、点差なし）
  no_margin: 点差を使わず、K値と scale だけを最適に調整したもの
  selected : 点差の倍率ありも含めて、調整期間で最良だったもの
             K = K固定 × (1 + 係数 × 点差割合)
 
同日の試合は、全試合の予測を先に行ってからレートを更新する（リーク防止）
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
TUNE_START = date(2022, 1, 1)   # 調整期間の開始（それ以前はレートの助走期間）
TEST_START = date(2024, 1, 1)   # 評価期間の開始（調整期間はこの前日まで）
INITIAL_ELO = 1500
 
NORMALIZE_NAMES = True          # 選手名の表記ゆれ（"[12345]"、姓名の順、大文字小文字）を統一する
DROP_DUPLICATES = True          # 同じ日・大会・組み合わせ・勝者の完全重複を除く
 
K_GRID = [16, 24, 32, 40, 50, 60, 80]
COEF_GRID = [1, 2, 4, 6, 8]            # 点差倍率の係数（0＝点差なしは別枠で調整する）
SCALE_GRID = [300, 350, 400, 450, 500] # 点差なし版でだけ調整する（Elo式の400の部分）
MARGIN_MODES = ["total", "per_game"]
# total   : 全ゲーム合計の点差 ÷ 全ゲーム合計の得点（CSVの検証で使ってきた定義）
# per_game: ゲームごとの「点差 ÷ そのゲームの合計得点」の平均（アプリの1ゲーム入力に近い定義）
 
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
 
 
def parse_margins(score):
    games = [(int(a), int(b)) for a, b in re.findall(r"(\d+)-(\d+)", score or "")]
    if not games:
        return 0.0, 0.0
    total_a = sum(a for a, _ in games)
    total_b = sum(b for _, b in games)
    total = abs(total_a - total_b) / (total_a + total_b) if total_a + total_b else 0.0
    per_game = sum(abs(a - b) / (a + b) for a, b in games if a + b) / len(games)
    return total, per_game
 
 
def load_matches():
    with DATA_FILE.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
 
    matches, seen = [], set()
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
 
        total, per_game = parse_margins(row.get("score", ""))
        matches.append({
            "date": match_date,
            "team1": team1,
            "team2": team2,
            "winner": winner,
            "margin": {"total": total, "per_game": per_game},
            "cluster": f"{row.get('tournament', '')}|{match_date.year}",
        })
 
    matches.sort(key=lambda m: m["date"])
    return matches
 
 
# ===== Elo =====
def win_probability(r1, r2, scale):
    return 1 / (1 + 10 ** ((r2 - r1) / scale))
 
 
def schedule_k(games):
    if games < 10:
        return 40
    if games < 30:
        return 24
    return 16
 
 
def run_model(matches, k_fixed=None, coef=0.0, mode="total", scale=400):
    """
    k_fixed=None なら元の設計（試合数別K）。
    返り値: TUNE_START 以降の試合の (日付, クラスタ, 予測確率, 結果) のリスト
    """
    rating = defaultdict(lambda: float(INITIAL_ELO))
    games = defaultdict(int)
    out = []
 
    for match_date, group in groupby(matches, key=lambda m: m["date"]):
        day = list(group)
 
        # 先にその日の全試合を予測する
        if match_date >= TUNE_START:
            for m in day:
                r1 = sum(rating[p] for p in m["team1"]) / 2
                r2 = sum(rating[p] for p in m["team2"]) / 2
                out.append((match_date, m["cluster"],
                            win_probability(r1, r2, scale),
                            1 if m["winner"] == 1 else 0))
 
        # 予測のあとで、順番にレートを更新する
        for m in day:
            r1 = sum(rating[p] for p in m["team1"]) / 2
            r2 = sum(rating[p] for p in m["team2"]) / 2
            e1 = win_probability(r1, r2, scale)
            s1 = 1 if m["winner"] == 1 else 0
 
            if k_fixed is None:
                g1 = sum(games[p] for p in m["team1"]) / 2
                g2 = sum(games[p] for p in m["team2"]) / 2
                k = (schedule_k(g1) + schedule_k(g2)) / 2
            else:
                k = k_fixed * (1 + coef * m["margin"][mode])
 
            delta = k * (s1 - e1)
            for p in m["team1"]:
                rating[p] += delta
                games[p] += 1
            for p in m["team2"]:
                rating[p] -= delta
                games[p] += 1
 
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
        "log_loss": sum(point_loss(p, y) for _, _, p, y in rows) / n,
        "brier": sum((p - y) ** 2 for _, _, p, y in rows) / n,
        "accuracy": sum((p >= 0.5) == (y == 1) for _, _, p, y in rows) / n,
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
    return result  # [(LogLoss差), (Brier差), (的中率差)] それぞれ (点推定, 下限, 上限)
 
 
# ===== メイン =====
def describe(cfg):
    if cfg["k_fixed"] is None:
        return "試合数別K(40→24→16)・点差なし"
    if cfg["coef"] == 0:
        return f"固定K={cfg['k_fixed']}・点差なし・scale={cfg['scale']}"
    return f"固定K={cfg['k_fixed']}・点差倍率 係数{cfg['coef']}（{cfg['mode']}）"
 
 
def main():
    matches = load_matches()
    print(f"読み込んだ試合数: {len(matches):,}（名前統一={NORMALIZE_NAMES}, 重複除去={DROP_DUPLICATES}）")
    print(f"調整期間: {TUNE_START} 〜 {TEST_START}（前日まで） / 評価期間: {TEST_START} 以降")
    print()
 
    original = {"k_fixed": None, "coef": 0.0, "mode": "total", "scale": 400}
 
    # --- 1. 調整期間だけで候補を比較 ---
    candidates = [original]
    for k, s in product(K_GRID, SCALE_GRID):
        candidates.append({"k_fixed": k, "coef": 0.0, "mode": "total", "scale": s})
    for k, c, mode in product(K_GRID, COEF_GRID, MARGIN_MODES):
        candidates.append({"k_fixed": k, "coef": c, "mode": mode, "scale": 400})
 
    tuned = []
    for cfg in candidates:
        out = run_model(matches, **cfg)
        tune_rows = in_period(out, TUNE_START, TEST_START)
        tuned.append((metrics(tune_rows)["log_loss"], cfg))
 
    tune_n = metrics(tune_rows)["n"]
    print(f"【調整期間】{tune_n:,}試合・{len(candidates)}通りを比較（Log Loss、小さいほど良い）")
    ranked = sorted(tuned, key=lambda t: t[0])
    for ll, cfg in ranked[:5]:
        print(f"  {ll:.4f}  {describe(cfg)}")
    print()
 
    selected = ranked[0][1]
    no_margin = min((t for t in tuned if t[1]["k_fixed"] is not None and t[1]["coef"] == 0),
                    key=lambda t: t[0])[1]
    original_tune_ll = next(ll for ll, cfg in tuned if cfg is original)
    print(f"元の設計    : {original_tune_ll:.4f}  {describe(original)}")
    print(f"点差なし最良: {min(ll for ll, c in tuned if c['k_fixed'] is not None and c['coef'] == 0):.4f}  {describe(no_margin)}")
    print(f"採用(selected): {ranked[0][0]:.4f}  {describe(selected)}")
    print()
 
    # --- 2. 評価期間で1回だけ評価 ---
    test = {}
    for name, cfg in [("original", original), ("no_margin", no_margin), ("selected", selected)]:
        out = run_model(matches, **cfg)
        test[name] = in_period(out, TEST_START)
 
    print(f"【評価期間】{len(test['selected']):,}試合（ここで1回だけ評価。結果を見てパラメータを変えない）")
    for name, label in [("original", "元の設計"), ("no_margin", "点差なし最良"), ("selected", "採用設定")]:
        m = metrics(test[name])
        print(f"  {label:10s} 的中率 {m['accuracy']:.2%}  Log Loss {m['log_loss']:.4f}  Brier {m['brier']:.4f}")
    print()
 
    # --- 3. 差の不確かさ ---
    print(f"【差の95%信頼区間】大会単位ブートストラップ {N_BOOT}回（マイナスのLog Loss/Brierは改善）")
    for a, b, label in [("original", "selected", "元の設計 → 採用設定"),
                        ("original", "no_margin", "元の設計 → 点差なし最良（K調整だけの効果）"),
                        ("no_margin", "selected", "点差なし最良 → 採用設定（点差倍率だけの効果）")]:
        if test[a] is test[b]:
            continue
        ll, br, acc = paired_bootstrap(test[a], test[b])
        print(f"  {label}")
        print(f"    Log Loss {ll[0]:+.4f} [{ll[1]:+.4f}, {ll[2]:+.4f}]  "
              f"Brier {br[0]:+.4f} [{br[1]:+.4f}, {br[2]:+.4f}]  "
              f"的中率 {acc[0]*100:+.2f}pt [{acc[1]*100:+.2f}, {acc[2]*100:+.2f}]")
    print()
 
    # --- 記録を保存 ---
    with DATA_FILE.with_name("tuning_results.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tune_log_loss", "k_fixed", "coef", "mode", "scale"])
        for ll, cfg in ranked:
            w.writerow([f"{ll:.5f}", cfg["k_fixed"] or "schedule", cfg["coef"], cfg["mode"], cfg["scale"]])
 
    with DATA_FILE.with_name("predictions_test_selected.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "cluster", "p_team1_win", "actual"])
        for d, c, p, y in test["selected"]:
            w.writerow([d.isoformat(), c, f"{p:.5f}", y])
 
    print("保存: tuning_results.csv（調整期間の全候補）, predictions_test_selected.csv（評価期間の予測）")
 
 
if __name__ == "__main__":
    main()