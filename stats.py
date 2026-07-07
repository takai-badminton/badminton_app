import math
from collections import defaultdict


def compute_all_stats(matches): # まとめて全部計算するよ関数、
    player_elo_data = player_elo(matches) # eloの辞書を作ってる
    pair_elo_data = pair_elo(matches)

    return { # 辞書を戻り値にしてる！下の９個のデータを１つの辞書にまとめてる、使い勝手がいい！！
        "player_elo": player_elo_data, 
        "pair_elo": pair_elo_data,
        "player_elo_sorted": 
            sorted( # 50音順にして、リストを返す（sorted()はリストを返す！！）
                player_elo_data.items(), # キー（名前）とバリュー（elo値）を取得して、タプルにして返す
                key=lambda x: x[1], # タプルの2番目（elo値）を基準に並べる
                reverse=True # 昇順から降順にして
            ),
        "pair_elo_sorted": 
            sorted(
                pair_elo_data.items(), 
                key=lambda x: x[1], 
                reverse=True
            ),
        "player_stats_data": player_stats(matches),
        "pair_stats_data": pair_stats(matches),
        "recent": recent_result(matches),
        "player_diff": player_diff_stats(matches),
        #"surprise_top5": surprise_ranking(matches, pair_elo_data),
    }


def player_elo_history(matches): # 各プレイヤーのelo推移グラフ用データ作成関数、eloの推移に使う
    elo = defaultdict(lambda: 1500) # 現在のelo保存用、まだキーがないときにアクセスしたら自動バリュー作成
    history = defaultdict(list) # 初アクセス時に空のリストをバリューとしてもつ、初期値

    for i, m in enumerate(reversed(matches)):  # enumerate():インデックス（0から）と値を同時に取り出す関数
                                               # load_data()でorder by id descしてるから最新→最古になってる、それを最古→最新にしてる、eloだから
        team1 = m["team1"] # matchesのteam1をteam1にいれて
        team2 = m["team2"]

        team1_avg = sum(elo[p] for p in team1) / 2 # eloのチーム平均とって
        team2_avg = sum(elo[p] for p in team2) / 2

        if m["score1"] > m["score2"]:
            score1 = 1 # 勝ったら1として
        else:
            score1 = 0 # 負けたら0

        new1, new2 = update_elo(team1_avg, team2_avg, score1) # 上で作ったデータをupdateにいれて、返ってきたのをnew1,new2とする

        for p in team1: # team1から一人ずつ取り出して
            elo[p] += (new1 - team1_avg) # 差を加えて
            history[p].append(elo[p]) # historyリストに新しいeloを加える、試合を重ねるごとに増える

        for p in team2:
            elo[p] += (new2 - team2_avg)
            history[p].append(elo[p])

    return history # historyを返す


def get_k(games): # 試合数によってK値を変える関数
    if games < 10: # 試合数少ないときは大きく変動させる
        return 40
    elif games < 30: # 安定してきたら変動を減らす
        return 24
    else:
        return 16 # それ以上ならこんなもんだと思う



def predict_win_rate(r1, r2): # eloの勝率予測関数
    return 1 / (1 + 10 ** ((r2 - r1) / 400)) # 公式


def predict_pair_vs_pair(pair1, pair2, pair_elo): # 2ペアの勝率計算関数
    r1 = pair_elo.get(tuple(sorted(pair1)), 1500) # pair1のelo取得、なければ初期化
    r2 = pair_elo.get(tuple(sorted(pair2)), 1500) # ソートして、順番統一してタプルにすることでキーに設定できる、ほんでそれのバリュー(elo値)を取得

    p1 = predict_win_rate(r1, r2) # eloの公式
    return p1 * 100 # %表記にする


def expected_score(ra, rb): # Aが勝つ確率を計算する関数
    return 1 / (1 + 10 ** ((rb - ra) / 400)) # 公式


def update_elo(ra, rb, score_a, k=32): # eloレート更新関数
    # k=32は使ってないけど一応残しとく
    ea = expected_score(ra, rb) # Aの期待勝率計算
    eb = 1 - ea # 余事象

    ra_new = ra + k * (score_a - ea) #Aのelo公式
    rb_new = rb + k * ((1 - score_a) - eb) # Bのelo公式

    return ra_new, rb_new # 返す


def pair_elo(matches): # ペアelo計算関数
    elo = defaultdict(lambda: 1500) # 初アクセス時に自動生成

    games_played = defaultdict(int) # defauldict(lambda:0)と同じ、0で初期化

    for m in reversed(matches): # 全試合見て
        t1 = tuple(sorted(m["team1"])) # ソートしてタプルにすることで入力順によらなくなる、同じペアとしてみなせる
        t2 = tuple(sorted(m["team2"]))

        r1 = elo[t1] # 現在のペアelo取得
        r2 = elo[t2]

        g1 = games_played[t1] # ペアの試合数取得
        g2 = games_played[t2]
        diff = abs(m["score1"] - m["score2"]) # 点差の絶対値とって
        bonus = diff / 10 # ボーナス分に組み込む、圧勝ならelo変動を大きくする

        k = (get_k(g1) + get_k(g2)) / 2 + bonus # k値決定

        if m["score1"] > m["score2"]: # 勝敗判定
            s1, s2 = 1, 0
        else:
            s1, s2 = 0, 1

        e1 = 1 / (1 + 10 ** ((r2 - r1) / 400)) # 二人の期待勝率の公式
        e2 = 1 - e1

        elo[t1] = r1 + k * (s1 - e1) # elo更新の公式
        elo[t2] = r2 + k * (s2 - e2)

        games_played[t1] += 1 # 試合数更新
        games_played[t2] += 1

    return dict(elo) # dictに戻して返す


def player_elo(matches): # 選手elo計算関数
    elo = defaultdict(lambda: 1500) # プレイヤーごとのeloを保存する辞書を作成
                                    # 存在しない選手（キー）なら1500（バリュー）をとして自動で作る
    games_played = defaultdict(int) # (=lambda: 0と同じ)各プレイヤーの試合数、intで初期化だから0が初期値

    for m in reversed(matches): # 全試合を順番に見る
        team1 = m["team1"] # チーム取得
        team2 = m["team2"]

        team1_avg = sum(elo[p] for p in team1) / 2 
        # elo[p] for p in team1　チーム1の各プレイヤーのeloを取り出す(リスト内包記法便利！！)
        # それをsumで足し合わせ、平均を取る
        team2_avg = sum(elo[p] for p in team2) / 2

        # K値調整
        g1 = sum(games_played[p] for p in team1) / 2 # 平均試合数
        g2 = sum(games_played[p] for p in team2) / 2
        diff = abs(m["score1"] - m["score2"]) # 点差計算、abs（絶対値）
        bonus = diff / 10 # 点差ボーナス、点差がつけばつくほどボーナスを大きくしてる
        # k値完成
        k = (get_k(g1) + get_k(g2)) / 2 + bonus
        # 上の方で定義したget_k()を引数を指定して呼び出し
        # チーム1とチーム2のk値を足して平均取ってボーナスを足す

        if m["score1"] > m["score2"]: #勝敗を0か1かにする
            score1 = 1 # 勝ち = 1,負け = 0　elo式で使う
        else:
            score1 = 0

        new1, new2 = update_elo(team1_avg, team2_avg, score1, k) # elo更新計算、上で作ったのを引数として渡す、戻り値としてnew1,new2
        # 分配するよ
        for p in team1: # チーム1の各プレイヤーのeloを更新
            elo[p] += (new1 - team1_avg) # チーム全体で増えた分を各個人に反映させる、個人差はそのまま
            games_played[p] += 1 #一試合追加更新

        for p in team2:
            elo[p] += (new2 - team2_avg)
            games_played[p] += 1
    return dict(elo) # 一応defauldictをdictに変換


def opponent_stats(matches, name): # （引数にnameも受け取る！！）だれがどのペアに強いか、弱いか関数
    stats = defaultdict(lambda: {"win": 0, "game": 0}) # 初アクセス時の辞書作成
    player_elo_data = player_elo(matches) # 選手のeloを取得

    for m in matches: # 全試合見て
        team1 = m["team1"] # 取り出して
        team2 = m["team2"]

        if name in team1 or name in team2: # team1かteam2のどちらかにname（この先呼び出されるplayer_detailでurlからきたプレイヤー名）があれば
                                           # 例えば、name = "田中"だったら、田中がteam1,2のいずれかにあればTrue、なければFalse
                                           # つまり、「この試合にその選手が出ていたら」って意味

            if m["score1"] > m["score2"]: # 勝者判定
                winners = team1
            else:
                winners = team2

            if name in team1: # 相手チーム取得
                opponent_team = team2
            else:
                opponent_team = team1

            opponent = tuple(opponent_team) # 辞書のキーにするためタプル化

            stats[opponent]["game"] += 1 # 試合数追加
            if name in winners: 
                stats[opponent]["win"] += 1 # "opponnentに対して"勝利カウンター1追加

    result = [] # 結果保存用空リスト

    for opp, s in stats.items(): # statsのキーとバリュー共に取り出す
        real_rate = s["win"] / s["game"] * 100
        my_elo = player_elo_data.get(name, 1500) # eloとってきてなかったら1500
        opp_elo = sum(player_elo_data.get(p, 1500) for p in opp) / 2 # 相手ペアelo平均取得

        expected = predict_win_rate(my_elo, opp_elo) * 100
        delta = real_rate - expected # 差分を計算
        result.append((opp, s["win"], s["game"], real_rate, delta))

    result.sort(key=lambda x: x[3], reverse=True) # real_rate順に降順（高い順）

    return result # resultを返す


def player_diff_stats(matches): # 各プレイヤーの得失点差傾向分析関数
    stats = defaultdict(lambda: {"diffs": []}) # 初アクセス時にこういうバリューを作る

    for m in matches: # 全試合一個ずつ見て
        team1 = m["team1"] # チームを取得して
        team2 = m["team2"]
        diff = m["score1"] - m["score2"] # 点差を計算して

        # team1側
        for p in team1:
            stats[p]["diffs"].append(diff) # statsのp（プレイヤー名）のdiffsのバリューにdiffを加える

        # team2側（team1の逆符号）
        for p in team2:
            stats[p]["diffs"].append(-diff) # 上の逆符号の数をdiffsに加える

        # ここまでで各プレイヤーの試合ごとの得失点差を記録している（例：{"田中": {"diffs": [6, -2, 4]},"佐藤": {"diffs": [6, 1, -5]}}）

    result = {} # 最終結果を入れる辞書を用意して

    for p, s in stats.items(): # .itemsでstatsのキーとバリュー両方取得してp,sに代入して回す
        diffs = s["diffs"] # 得失点差一覧を取得して
        n = len(diffs) # 試合数取得

        if n > 0: # 計算
            mean = sum(diffs) / n # 平均
            var = sum((x - mean)**2 for x in diffs) / n # 分散
            std = math.sqrt(var) # 標準偏差
        else: # 0で割ることの対策
            mean = 0
            var = 0
            std = 0

        if std < 3: # 安定度判定
            stability = "安定"
        elif std < 6:
            stability = "普通"
        else:
            stability = "波が激しい"    

        result[p] = { # 結果保存
            "mean": mean, # 上で計算したものを格納！
            "var": var,
            "std": std,
            "games": n,
            "stability": stability
        }

    return result


def recent_result(matches, n=5): # 直近五試合の成績関数
    recent = matches[:n] # matchesリストの先頭からn個取り出す、load_data()でORDER BY id DESCにしてたから新しい試合順になってる

    wins = 0 # 初期化
    for m in recent: # recentから一個ずつ取り出して下の処理
        if m["score1"] > m["score2"]:
            wins += 1

    total = len(recent) # recentの長さをカウントして
    win_rate = (wins / total * 100) if total > 0 else 0 # 0devideエラー防止

    return {
        "games": total,
        "wins": wins,
        "win_rate": round(win_rate, 1)
    }


def pair_stats(matches): # ペアの成績関数
    stats = defaultdict(lambda: {"win": 0, "game": 0}) # 下と同様
    
    for m in matches:
        t1 = tuple(sorted(m["team1"])) # sorted:ソートして毎回同じ順番にする、田中・佐藤と佐藤・田中を同じとみなす
        t2 = tuple(sorted(m["team2"])) # tuple:不変のタプルにする。これで二人で一つの辞書のキーにできる

        if m["score1"] > m["score2"]: # 勝敗判定
            winner = t1
        else:
            winner = t2

        stats[t1]["game"] += 1 # t1のバリューにあるgameっていうキーを指定して、そのバリューに1加える,辞書in辞書
        stats[t2]["game"] += 1

        stats[winner]["win"] += 1 # winerのバリューにあるwinっていうキーを指定して、そのバリューに1加える、辞書in辞書

    return stats # statsを返す


def player_stats(matches): # matchesを引数にしてプレイヤー成績作成する関数
    stats = defaultdict(lambda: {"win": 0, "game": 0}) # {"win": 0, "game": 0}:新しいキーがアクセスされたときに生成される辞書の初期値,辞書in辞書

    for m in matches: # 1試合ずつみる
        team1 = m["team1"] # チーム取り出し
        team2 = m["team2"]

        if m["score1"] > m["score2"]: # 勝敗判定
            winners = team1
        else:
            winners = team2

        for p in team1 + team2: # リスト結合して左から順に選手を取り出して、その選手の試合数を+1することを選手ごとに繰り返す
            stats[p]["game"] += 1

        for p in winners: # 上でできた勝者だけループさせる
            stats[p]["win"] += 1 # 勝ちカウンターを+1

    return stats # statsを返す
