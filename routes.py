from flask import redirect, render_template, request, url_for

from db import add_match, delete_match, load_data
from stats import (
    compute_all_stats,
    opponent_stats,
    player_diff_stats,
    player_elo,
    player_elo_history,
    player_stats,
    recent_result,
)
from utils import normalize_name


def register_routes(app):
    @app.route("/")
    def index():
        matches = load_data()
        stats = compute_all_stats(matches)
        total = len(matches)

        team1_wins = 0
        for m in matches:
            if int(m["score1"]) > int(m["score2"]):
                team1_wins += 1

        win_rate = round(team1_wins / total * 100, 1) if total > 0 else 0
        error = request.args.get("error")

        return render_template(
            "index.html",
            matches=matches,
            total=total,
            win_rate=win_rate,
            player_stats_data=stats["player_stats_data"],
            pair_stats=stats["pair_stats_data"],
            recent=stats["recent"],
            player_diff=stats["player_diff"],
            player_elo_sorted=stats["player_elo_sorted"],
            pair_elo_sorted=stats["pair_elo_sorted"],
            error=error,
        )

    @app.route("/add", methods=["POST"])
    def add():
        if request.form["score1"] == "" or request.form["score2"] == "":
            return redirect(url_for("index", error="スコアを入力して下さい"))

        score1 = int(request.form["score1"])
        score2 = int(request.form["score2"])

        if score1 < 0 or score2 < 0:
            return redirect(url_for("index", error="スコアは0以上にしてください"))

        if score1 > 30 or score2 > 30:
            return redirect(url_for("index", error="スコアは30以下にしてください"))

        p1 = normalize_name(request.form["team1_p1"])
        p2 = normalize_name(request.form["team1_p2"])
        p3 = normalize_name(request.form["team2_p1"])
        p4 = normalize_name(request.form["team2_p2"])

        all_players = [p1, p2, p3, p4]

        if "" in all_players:
            return redirect(url_for("index", error="選手名を入力して下さい"))

        if len(set(all_players)) != 4:
            return redirect(url_for("index", error="同じ選手名は使用できません"))

        add_match(p1, p2, p3, p4, score1, score2)
        return redirect(url_for("index"))

    @app.route("/delete/<int:id>", methods=["POST"])
    def delete(id):
        delete_match(id)
        return redirect(url_for("index"))

    @app.route("/predict", methods=["GET", "POST"])
    def predict():
        result = None
        error = request.args.get("error")

        if request.method == "POST":
            p1 = normalize_name(request.form["team1_p1"])
            p2 = normalize_name(request.form["team1_p2"])
            p3 = normalize_name(request.form["team2_p1"])
            p4 = normalize_name(request.form["team2_p2"])

            all_players = [p1, p2, p3, p4]

            if "" in all_players:
                return redirect(url_for("predict", error="選手名を入力して下さい"))

            if len(set(all_players)) != 4:
                return redirect(url_for("predict", error="同じ選手名は使用できません"))

            matches = load_data()
            stats = compute_all_stats(matches)
            player_elo_data = stats["player_elo"]
            pair_elo_data = stats["pair_elo"]

            valid_players = set(player_elo_data.keys())
            unknown = []
            for p in all_players:
                if p not in valid_players:
                    unknown.append(p)

            if unknown:
                return redirect(url_for("predict", error="存在しない選手: " + ", ".join(unknown)))

            pair1 = tuple(sorted([p1, p2]))
            pair2 = tuple(sorted([p3, p4]))

            r_pair1 = pair_elo_data.get(pair1, 1500)
            r_pair2 = pair_elo_data.get(pair2, 1500)
            r_ind1 = (player_elo_data.get(p1, 1500) + player_elo_data.get(p2, 1500)) / 2
            r_ind2 = (player_elo_data.get(p3, 1500) + player_elo_data.get(p4, 1500)) / 2

            alpha = 0.7
            r1 = alpha * r_pair1 + (1 - alpha) * r_ind1
            r2 = alpha * r_pair2 + (1 - alpha) * r_ind2

            prob = 1 / (1 + 10 ** ((r2 - r1) / 400))
            result = round(prob * 100, 1)

        return render_template("predict.html", result=result, error=error)

    @app.route("/player/<name>")
    def player_detail(name):
        name = normalize_name(name)
        matches = load_data()
        history_all = player_elo_history(matches)
        player_history = history_all.get(name, [])

        player_matches = []
        for m in matches:
            if name in m["team1"] or name in m["team2"]:
                player_matches.append(m)

        player_elo_data = player_elo(matches)
        elo_value = player_elo_data.get(name, 1500)
        stats = player_stats(player_matches)
        d = player_diff_stats(player_matches).get(
            name,
            {"mean": 0, "var": 0, "std": 0, "games": 0, "stability": "不明"},
        )
        recent = recent_result(player_matches)
        opp_stats = opponent_stats(player_matches, name)
        player_elo_sorted = sorted(player_elo_data.items(), key=lambda x: x[1], reverse=True)

        rank = None
        for i, (n, e) in enumerate(player_elo_sorted):
            if n == name:
                rank = i + 1
                break

        if len(player_matches) < 10:
            trust = "低"
        elif len(player_matches) < 30:
            trust = "中"
        else:
            trust = "高"

        return render_template(
            "player_detail.html",
            name=name,
            matches=player_matches,
            stats=stats,
            d=d,
            recent=recent,
            opp_stats=opp_stats,
            elo=elo_value,
            rank=rank,
            trust=trust,
            history=player_history,
        )
