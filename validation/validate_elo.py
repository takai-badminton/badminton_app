# 初期検証用スクリプト。
# 個人Elo・ペアElo・混合方式を比較した探索実験。
# 現在の最終設計の根拠には使用しない。

import csv
import re
from collections import defaultdict
from datetime import date
from itertools import groupby
from math import log
from pathlib import Path


# 設定
DATA_FILE = Path(__file__).with_name("md.csv")
TEST_START = date(2024, 1, 1)
INITIAL_ELO = 1500
ALPHA = 0.7  # ペアEloの比率

# 個人Eloの設定を2通り比較する
#   "old": 試合数でKを変える（40→24→16）、点差は使わない  ← 検証開始時点のアプリ相当
#   "new": 固定K=40 × (1 + 4 × 点差割合)                   ← stats.py の match_k と同じ
K_BASE = 40
MARGIN_COEF = 4
CONFIGS = [
    ("old", "改善前（試合数でK変化・点差なし）"),
    ("new", "改善後（固定K×点差倍率）"),
]


def get_k(games):
    """現在のアプリと同じ試合数別K値"""
    if games < 10:
        return 40
    elif games < 30:
        return 24
    return 16


def win_probability(r1, r2):
    """Eloからチーム1の勝率を計算"""
    return 1 / (1 + 10 ** ((r2 - r1) / 400))


def split_team(value):
    """CSVのペア名を2人のリストに変換"""
    players = [name.strip() for name in value.split("/")]
    if len(players) != 2 or not all(players):
        raise ValueError(f"ペア名を解析できません: {value}")
    return players


def parse_margin(score):
    """'21-10 21-8' のようなスコア文字列から、点差割合（総得失点差/総得点）を返す"""
    games = re.findall(r"(\d+)-(\d+)", score or "")
    a = sum(int(x) for x, _ in games)
    b = sum(int(y) for _, y in games)
    return abs(a - b) / (a + b) if a + b > 0 else 0.0


def load_matches():
    """CSVを読み込み、日付順に並べる"""
    with DATA_FILE.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    matches = []

    for row in rows:
        try:
            match_date = date.fromisoformat(row["date"])
            team1 = split_team(row["team1"])
            team2 = split_team(row["team2"])
            winner = int(row["winner"])

            if winner not in (1, 2):
                continue

            if len(set(team1 + team2)) != 4:
                continue

            matches.append({
                "date": match_date,
                "team1": team1,
                "team2": team2,
                "winner": winner,
                "margin": parse_margin(row.get("score", "")),
                "tournament": row.get("tournament", ""),
            })
        except (ValueError, KeyError):
            continue

    matches.sort(key=lambda m: m["date"])
    return matches


def new_ratings():
    return {
        "player": defaultdict(lambda: INITIAL_ELO),
        "pair": defaultdict(lambda: INITIAL_ELO),
        "player_games": defaultdict(int),
        "pair_games": defaultdict(int),
    }


def predict(match, ratings):
    """試合前のレートから3方式の勝率を計算"""
    team1 = match["team1"]
    team2 = match["team2"]

    player = ratings["player"]
    pair = ratings["pair"]

    pair1 = tuple(sorted(team1))
    pair2 = tuple(sorted(team2))

    individual1 = sum(player[p] for p in team1) / 2
    individual2 = sum(player[p] for p in team2) / 2

    pair1_elo = pair[pair1]
    pair2_elo = pair[pair2]

    combined1 = ALPHA * pair1_elo + (1 - ALPHA) * individual1
    combined2 = ALPHA * pair2_elo + (1 - ALPHA) * individual2

    return {
        "player_only": win_probability(individual1, individual2),
        "pair_only": win_probability(pair1_elo, pair2_elo),
        "combined": win_probability(combined1, combined2),
    }


def update_ratings(match, ratings, config):
    """試合結果で個人EloとペアEloを別々に更新"""
    team1 = match["team1"]
    team2 = match["team2"]
    score1 = 1 if match["winner"] == 1 else 0

    player = ratings["player"]
    pair = ratings["pair"]
    player_games = ratings["player_games"]
    pair_games = ratings["pair_games"]

    # 個人Elo：チーム内の個人レート平均で勝敗を評価
    r1 = sum(player[p] for p in team1) / 2
    r2 = sum(player[p] for p in team2) / 2

    if config == "new":
        k = K_BASE * (1 + MARGIN_COEF * match["margin"])
    else:
        g1 = sum(player_games[p] for p in team1) / 2
        g2 = sum(player_games[p] for p in team2) / 2
        k = (get_k(g1) + get_k(g2)) / 2

    e1 = win_probability(r1, r2)
    new1 = r1 + k * (score1 - e1)
    new2 = r2 + k * ((1 - score1) - (1 - e1))

    for p in team1:
        player[p] += new1 - r1
        player_games[p] += 1

    for p in team2:
        player[p] += new2 - r2
        player_games[p] += 1

    # ペアElo：固定ペアごとに独立して更新
    pair1 = tuple(sorted(team1))
    pair2 = tuple(sorted(team2))

    r1 = pair[pair1]
    r2 = pair[pair2]

    g1 = pair_games[pair1]
    g2 = pair_games[pair2]
    k = (get_k(g1) + get_k(g2)) / 2

    e1 = win_probability(r1, r2)
    pair[pair1] = r1 + k * (score1 - e1)
    pair[pair2] = r2 + k * ((1 - score1) - (1 - e1))

    pair_games[pair1] += 1
    pair_games[pair2] += 1


def calculate_metrics(records, key):
    """的中率・Log Loss・Brier Scoreを計算"""
    if not records:
        return None

    correct = 0
    log_loss = 0.0
    brier = 0.0
    eps = 1e-15

    for row in records:
        p = row[key]
        actual = row["actual"]

        if (p >= 0.5) == (actual == 1):
            correct += 1

        p_safe = min(max(p, eps), 1 - eps)
        log_loss -= (
            actual * log(p_safe)
            + (1 - actual) * log(1 - p_safe)
        )
        brier += (p - actual) ** 2

    n = len(records)

    return {
        "games": n,
        "accuracy": correct / n,
        "log_loss": log_loss / n,
        "brier": brier / n,
    }


def evaluate(matches, config):
    """1つの設定で全試合を日付順に処理し、2024年以降の予測結果を返す"""
    ratings = new_ratings()
    evaluated = []

    # 同じ日付の試合は、全試合の予測を先に行う。
    # 同日内の結果が同日の別試合の予測に漏れないようにする。
    for match_date, group in groupby(matches, key=lambda m: m["date"]):
        day_matches = list(group)
        day_predictions = []

        for match in day_matches:
            probabilities = predict(match, ratings)

            if match_date >= TEST_START:
                day_predictions.append({
                    "date": match_date.isoformat(),
                    "team1": " / ".join(match["team1"]),
                    "team2": " / ".join(match["team2"]),
                    "actual": 1 if match["winner"] == 1 else 0,
                    **probabilities,
                })

        # 予測が終わってから、その日の試合結果でレートを更新
        for match in day_matches:
            update_ratings(match, ratings, config)

        evaluated.extend(day_predictions)

    return evaluated


def main():
    matches = load_matches()

    if not matches:
        print("試合データを読み込めませんでした。CSVを確認してください。")
        return

    print(f"読み込んだ試合数: {len(matches):,}")
    print(f"評価対象期間: {TEST_START.isoformat()} 以降")
    print()

    models = [
        ("player_only", "個人Eloのみ"),
        ("pair_only", "ペアEloのみ"),
        ("combined", "個人・ペアElo 70:30"),
    ]

    results = {}
    for config, config_label in CONFIGS:
        evaluated = evaluate(matches, config)
        results[config] = evaluated

        print("=" * 40)
        print(f"{config_label}  評価試合数: {len(evaluated):,}")
        print("=" * 40)

        for key, label in models:
            result = calculate_metrics(evaluated, key)

            if result is None:
                print(f"{label}: 評価対象の試合がありません")
                continue

            print(f"【{label}】")
            print(f"的中率    : {result['accuracy']:.2%}")
            print(f"Log Loss  : {result['log_loss']:.4f}")
            print(f"Brier     : {result['brier']:.4f}")
            print()

    # 後から分析できるよう、改善後(new)の予測結果を保存する
    output_file = DATA_FILE.with_name("predictions_2024_onward.csv")

    with output_file.open("w", encoding="utf-8-sig", newline="") as f:
        fields = [
            "date", "team1", "team2", "actual",
            "player_only", "pair_only", "combined",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results["new"])

    print(f"予測結果（改善後）を保存しました: {output_file}")


if __name__ == "__main__":
    main()