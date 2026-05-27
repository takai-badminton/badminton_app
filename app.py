from flask import Flask, render_template, request, redirect
import sqlite3
from collections import defaultdict # 存在しないキーでバリューを取り出そうとしてもエラーにならない、勝手に初期値をバリューとして作成、保存してくれる、すごい！！
import math

app = Flask(__name__)


def compute_all_stats(matches):
    player_elo_data = player_elo(matches)
    pair_elo_data = pair_elo(matches)

    return {
        "player_elo": player_elo_data,
        "pair_elo": pair_elo_data,
        "player_elo_sorted": sorted(player_elo_data.items(), key=lambda x: x[1], reverse=True),
        "pair_elo_sorted": sorted(pair_elo_data.items(), key=lambda x: x[1], reverse=True),
        "player_stats_data": player_stats(matches),
        "pair_stats_data": pair_stats(matches),
        "recent": recent_result(matches),
        "player_diff": player_diff_stats(matches),
        "surprise_top5": surprise_ranking(matches, pair_elo_data),
    }


def player_elo_history(matches): # 各プレイヤーのelo推移グラフ用データ作成関数
    elo = defaultdict(lambda: 1500) # 現在のelo保存用、まだキーがないときにアクセスしたら自動バリュー作成
    history = defaultdict(list) # 初アクセス時に空のリストをバリューとしてもつ、初期値

    for i, m in enumerate(reversed(matches)):  # 
        team1 = m["team1"]
        team2 = m["team2"]

        team1_avg = sum(elo[p] for p in team1) / 2
        team2_avg = sum(elo[p] for p in team2) / 2

        if m["score1"] > m["score2"]:
            score1 = 1
        else:
            score1 = 0

        new1, new2 = update_elo(team1_avg, team2_avg, score1)

        for p in team1:
            elo[p] += (new1 - team1_avg)
            history[p].append(elo[p])

        for p in team2:
            elo[p] += (new2 - team2_avg)
            history[p].append(elo[p])

    return history


def get_k(games): # 試合数によってK値を変える関数
    if games < 10: # 試合数少ないときは大きく変動させる
        return 40
    elif games < 30: # 安定してきたら変動を減らす
        return 24
    else:
        return 16 # それ以上ならこんなもんだと思う


def surprise(prob): # 驚き度計算関数、引数は実際に起きた確率
    return -math.log2(prob) # 情報量(self-information)より採用


def surprise_ranking(matches, pair_elo_data): # 番狂わせランキング関数
    results = [] # 結果を入れる箱用意して

    for m in matches: # 全試合見て
        t1 = tuple(sorted(m["team1"])) # キーにできるように加工して
        t2 = tuple(sorted(m["team2"]))

        r1 = pair_elo_data.get(t1, 1500) # ペアeloを取得、なければ初期化して
        r2 = pair_elo_data.get(t2, 1500)

        # 勝率予測
        p1 = predict_win_rate(r1, r2) #下で作ったやつ使って勝率出して

        # 実際の結果
        if m["score1"] > m["score2"]: # 実際にどっちが勝ったか見る
            actual = 1 # team1が勝ったら1とする
        else:
            actual = 0

        # 実際に起きた側の確率を使う（team1が勝ったらp1,負けたら1 - p1）
        prob = p1 if actual == 1 else (1 - p1)

        s = surprise(prob) # 上の驚き度関数使って

        results.append((m, s, prob)) # 結果保存

    results.sort(key=lambda x: x[1], reverse=True) # s（驚き度）順に並べる、降順で

    return results[:5] # 上位五つ返す


def predict_win_rate(r1, r2): # eloの勝率予測関数
    return 1 / (1 + 10 ** ((r2 - r1) / 400)) # 公式


def predict_pair_vs_pair(pair1, pair2, pair_elo): # 2ペアの勝率計算関数
    r1 = pair_elo.get(tuple(sorted(pair1)), 1500) # pair1のelo取得、なければ初期化
    r2 = pair_elo.get(tuple(sorted(pair2)), 1500) # ソートして、順番統一してタプルにすることでキーに設定できる、ほんでそれのバリュー(elo値)を取得

    p1 = predict_win_rate(r1, r2) # eloの公式
    return p1 * 100 # %表記にする


@app.route('/predict', methods=['GET', 'POST']) # GET:ページを見る,POST:フォーム送信されたのを受け取る
def predict():
    result = None # 最初は結果なし

    if request.method == 'POST': # フォーム送信されたときだけ下の処理
        p1 = request.form['team1_p1'].strip() # フォームから名前取得
        p2 = request.form['team1_p2'].strip() #strip()で前後の空欄削除
        p3 = request.form['team2_p1'].strip()
        p4 = request.form['team2_p2'].strip()

        all_players = [p1, p2, p3, p4] # リスト化して後でチェックしやすくする

        # 未入力チェック
        if "" in all_players: # 空文字があれば下の処理
            return redirect('/predict?error=未入力の選手があります')

        # 同一人物チェック
        if len(set(all_players)) != 4:
            # set():重複消える
            # で、長さが4じゃなかったら重複ありだから下の処理
            return redirect('/predict?error=同じ選手名は使用できません')

        matches = load_data() #dbから全試合の情報を取得
        stats = compute_all_stats(matches) # 統計全部取得（辞書になって戻ってくる）

        # Elo取得
        player_elo_data = stats["player_elo"] # statsのplayerのバリュー取得
        pair_elo_data = stats["pair_elo"] # statsのpair_eloのバリュー取得

        #既存プレイヤーかチェック
        valid_players = set(player_elo_data.keys()) # player_elo_data辞書からキーのみ取得しsetして重複キャンセル

        unknown = [] #　空のリスト用意して

        for p in all_players:
            if p not in valid_players: #　上で作った既存プレイヤー中に入ってなかったらunknownに追加
                unknown.append(p) # 存在しない選手を抽出（リスト内包表記だとunknown = [p for p in all_players if p not in valid_players]）

        if unknown: # アンノウンのリストになんか入ってたら、それを存在しない選手として表示
            return redirect('/predict?error=存在しない選手: ' + "、".join(unknown)) #join()でリスト内を文字連結
        
        # ペア作成
        pair1 = tuple(sorted([p1, p2])) # sortedして表記そろえて、キーにするためにタプルにする
        pair2 = tuple(sorted([p3, p4]))

        # ペアElo
        r_pair1 = pair_elo_data.get(pair1, 1500) # pair1のeloを取得、なかったら1500にする
        r_pair2 = pair_elo_data.get(pair2, 1500) # 上でもペアeloとってるけど、そこからさらに細かくとる

        # 個人Elo（平均）
        r_ind1 = (player_elo_data.get(p1, 1500) + player_elo_data.get(p2, 1500)) / 2 # 各elo取得して平均取ってる、なかったら初期値設定
        r_ind2 = (player_elo_data.get(p3, 1500) + player_elo_data.get(p4, 1500)) / 2

        # 合成
        alpha = 0.7 # 70%をペアとして、30%を各個人の平均として
        r1 = alpha * r_pair1 + (1 - alpha) * r_ind1
        r2 = alpha * r_pair2 + (1 - alpha) * r_ind2

        # 勝率
        prob = 1 / (1 + 10 ** ((r2 - r1) / 400)) #elo公式

        result = round(prob * 100, 1) # %表記にして、1の位まで丸める

    return render_template('predict.html', result=result) # 計算したresultをpredict.htmlに送ってる


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

    for m in matches: # 全試合見て
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

    for m in matches: # 全試合を順番に見る
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

# 一旦使わない↓
"""def player_ranking(matches): # 各プレイヤー勝率ランキング
    stats = defaultdict(lambda: {"win": 0, "game": 0}) # 始めてアクセスされるキーがあったらバリューに{"win": 0, "game": 0}を自動作成

    for m in matches: # 1試合ずつ見て
        team1 = m["team1"] # team1,2を取り出して
        team2 = m["team2"]

        if m["score1"] > m["score2"]:
            winners = team1
        else:
            winners = team2

        for p in team1 + team2: # リスト結合させて左から順にみてく
            stats[p]["game"] += 1 #statsのpていうキーのバリューのgameっていうキーのバリューを指定して1加える

        for p in winners: # 勝者だけ1カウント
            stats[p]["win"] += 1

    ranking = [] # ランキング用リストを作って

    for name, s in stats.items(): #辞書.itemsでキーとバリューの両方とりだして、それぞれname,sに代入して回す
        if s["game"] > 0:
            rate = s["win"] / s["game"] * 100
            ranking.append((name, s["win"], s["game"], rate)) # 追加

    ranking.sort(key=lambda x: x[3], reverse=True) # 0,1,2,3番目のrateを基準に、降順でソート

    return ranking # ランキングを返す
"""

# 一旦使わない↓
"""
def pair_ranking(matches): # ペア勝率ランキング関数

    stats = defaultdict(lambda: {"win": 0, "game": 0}) # 存在しないキーが来たらバリューにこれ作っとく

    for m in matches: # 1試合ずつ取り出して
        t1 = tuple(sorted(m["team1"])) # ソートしてタプルにしてペア化する
        t2 = tuple(sorted(m["team2"]))

        if m["score1"] > m["score2"]:
            winner = t1
        else:
            winner = t2

        stats[t1]["game"] += 1
        stats[t2]["game"] += 1
        stats[winner]["win"] += 1

    ranking = [] # ここから上で処理したものをランキングにする、空のリストを用意

    for pair, s in stats.items(): #.items()statsのでキーとバリューを同時取得（pair(=(例：田中、佐藤)),s(={例：win:1,game:1})としてそれぞれ取得）
        if s["game"] > 0:
            win_rate = s["win"] / s["game"] * 100
            ranking.append((pair, s["win"], s["game"], win_rate)) # この4情報をリストに追加

    ranking.sort(key=lambda x: x[3], reverse=True)
    # key=lambda x: x[3]→何を基準にソートするか、0:pair,1:win,2:game,3:rateだからrateつまり勝率でソートする
    # reverse=True→Trueなら降順、Falseなら昇順
    return ranking[:5] # 上位5つだけ
"""

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


# 試合データを読み込む関数、下のindex()で呼び出すために先に準備してる
# データベースからデータとってきてFlaskで使える形に変換する関数
def load_data():
    conn = sqlite3.connect('matches.db') # matches.dbというデータベースに接続、なければ作る、データベース本体
    c = conn.cursor() # SQLを実行するための操作役conn（データベース）のcursor（操作する人）

    c.execute("""
              SELECT id, team1_p1, team1_p2,team2_p1, team2_p2, score1, score2, created_at FROM matches ORDER BY id DESC
              """)
    # SELECT（データを取る） id（列）, team1_p1（列）, ...から取る
    # もしSELECT * FROM matchesなら全部の列を取る
    # ORDER BY id DESC　並び変える(ORDER BY)、idを使って(id)、大きい順で（降順）(DESC)→最新の試合が一番上に来る
    rows = c.fetchall()
    # fetchall:SELECT文で取り出した結果を全部もらう、タプルが詰まったリストに変換
    conn.close() # データベースとの接続を切る

    matches = [] #空のリストを用意、ここに結果をためてく
    for r in rows:
        matches.append({
            "id": r[0],
            "team1": [r[1], r[2]],
            "team2": [r[3], r[4]],
            "score1": r[5],
            "score2": r[6],
            "created_at": r[7][:16]} )
        #　タプルを辞書に変換してmatchesに追加（辞書が詰まったリストになる）

    return matches


def init_db(): # 起動時に一回だけテーブル作ってる、アクセスのたびに作ってるわけではない
    conn = sqlite3.connect('matches.db') # matches.dbというデータベースに接続、なければ作る、データベース本体
    c = conn.cursor()  # SQLを実行するための操作役conn（データベース）のcursor（操作する人）

    c.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team1_p1 TEXT,
            team1_p2 TEXT,
            team2_p1 TEXT,
            team2_p2 TEXT,
            score1 INTEGER,
            score2 INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
    ''')
      # c（上で作ったデータベースを操作する人）.execute（実行するよ）（"SQL文"）        
      # IF NOT EXISTS:matchesっていう表を作れ（なければ）
      # ()の中：列名（カラム）を定義してる
      # id（自動で番号振る列） INTEGER（整数） PRIMARY KEY（この列が主役、同じ値はダメ、一行に一つだけ） AUTOINCREMENT（自動で増える、縦に1,2,3...みたいに）
      # team1_p1 text:文字列
      # TEXT, INTEGER:型の名前
      # created_at（列名、試合が作られた時刻を保存する場所）
      # TIMESTAMP（データの型、日時を保存する型、例：2026-05-07 14:32:10） 
      # DEFAULT CURRENT_TIMESTAMP（値が指定されなかったら、今の時刻を自動で入れる）


    conn.commit() # 変更を確定して保存する（これないと保存されない）
    conn.close()  # データベースとの接続を終了する、conn = sqlite3.connect('matches.db')でデータベースに接続したから、接続を切らないといけない（メモリ過多、バグるetc..）
    # まとめ：connect → 開く（接続する）、execute → 命令、commit → 保存、close → 閉じる
    

@app.route('/') # Flaskの文法、末尾が / （トップページ）なら、すぐ下の関数を動かせ！」の指示
def index(): # アクセスがきたらこの関数を起動
    matches = load_data() # DBから滑って読み込む、load_data()が作った辞書いっぱいのリストをmachesに入れてる
    stats = compute_all_stats(matches)
    player_elo_data = stats["player_elo"]
    pair_elo_data = stats["pair_elo"]
    total = len(matches) # machesリストの中に辞書が何個あるか（何試合か）カウントしてる
    team1_wins = 0 # 勝ち数カウンターを用意、最初は0
    for m in matches: # リストの中から、辞書（1試合分のデータ）を m という名前で一つずつ取り出すことを辞書の数（試合数）だけ繰り返す
        if int(m['score1']) > int(m['score2']): # 辞書 m の中から「自分の点数」と「相手の点数」を取り出して比較する
            team1_wins += 1 # (wins = wins + 1)
    
    val = (team1_wins / total * 100) if total > 0 else 0 # 勝率、0で割ることを防ぐ
    win_rate = round(val, 1) # 小数点第1位まで丸めた
    # 三項演算子,A if 条件 else B という書き方で、「もし条件に合えばA、そうでなければB」 という処理を一行で書ける
    # errorを受け取る
    error = request.args.get('error') # addからurlに乗っかってきたerrorを取り出す

    return render_template('index.html',
                            matches=matches,
                            total=total,
                            win_rate=win_rate,
                            player_stats_data=stats["player_stats_data"],
                            pair_stats=stats["pair_stats_data"],
                            recent=stats["recent"],
                            player_diff=stats["player_diff"],
                            player_elo_sorted=stats["player_elo_sorted"],
                            pair_elo_sorted=stats["pair_elo_sorted"],
                            surprise_top5=stats["surprise_top5"],
                            error=error
    )
    # Python側で作ったすべてのデータをhtmlにおくってる

@app.route('/add', methods=['POST']) 
# /add という場所に来たデータは、この下の add() 関数で処理するで
# html文に記述あり、/addは住所みたいなもの
# methods=['POST'] ＝ データの送り方
# GET（ゲット）: 「ページを見せて！」という普通のお願い。
# POST（ポスト）: 「このデータを登録して！」という、荷物を伴うお願い。
def add():
    # 画面から送られてきたデータを受け取る
    # 空欄チェック
    if request.form['team1_p1'] == "" or request.form['team1_p2'] == "" or request.form['team2_p1'] == "" or request.form['team2_p2'] == "":
        return redirect('/?error=相手名を入力してください') # トップページにエラー情報をくっつけて戻る

    if request.form['score1'] == "" or request.form['score2'] == "":
        return redirect('/?error=スコアを入力してください')

    score1 = int(request.form['score1'])
    score2 = int(request.form['score2'])
    
    if score1 < 0 or score2 < 0:
        return redirect('/?error=スコアは0以上にしてください')
    
    if score1 > 30 or score2 > 30:
        return redirect('/?error=スコアは30以下にしてください')

    p1 = request.form['team1_p1'].strip() # .strip()括弧の中（空白）を削除して値を取ってくる
    p2 = request.form['team1_p2'].strip()
    p3 = request.form['team2_p1'].strip()
    p4 = request.form['team2_p2'].strip()

    data = (
        p1,
        p2,
        p3,
        p4,
        int(request.form['score1']),
        int(request.form['score2'])
    ) # sqlに渡す用にタプルを作る

    conn = sqlite3.connect('matches.db') # matches.dbというデータベースに接続する、なければ作る
    c = conn.cursor() # 操作の役割、conn（データベース）のcursor（指示役）

    c.execute( #次のsql文を実行してください
    # matchesテーブルに1行データを追加してる命令
    """
    INSERT INTO matches(team1_p1, team1_p2, team2_p1, team2_p2, score1, score2)
    VALUES (?, ?, ?, ?, ?, ?)""", data)
    # この6列にデータ入れてね
    # VALUES (?, ?, ?):後で値を入れる場所、？：プレースホルダー（空欄）
    # execute(SQL, データ):sqlとデータは別で渡す
    conn.commit() # 保存！！
    conn.close() # データベースとの接続を閉じる！！    
    return redirect('/') # トップページ(/)に戻れ！！→index()起動


@app.route('/delete/<int:id>') # /delete/3みたいなurlがきたらid=3 を取り出して delete(id) に渡す
def delete(id): # idにurlの数字が入る
    conn = sqlite3.connect('matches.db')
    c = conn.cursor()

    c.execute("DELETE FROM matches WHERE id = ?", (id,)) # idが一致する行を削除
    # WHEREがないとデータすべて消えちゃう
    # (id,):一要素タプル、カンマがいる
    conn.commit() # 削除確定
    conn.close() # DB閉じる

    return redirect('/') # トップに戻る


@app.route('/player/<name>')
def player_detail(name):
    matches = load_data()
    history_all = player_elo_history(matches)
    player_history = history_all.get(name, [])
    # その人が出た試合だけ抽出
    player_matches = []
    for m in matches:
        if name in m["team1"] or name in m["team2"]:
            player_matches.append(m)

    # 統計
    player_elo_data = player_elo(matches)
    elo_value = player_elo_data.get(name, 1500)
    stats = player_stats(player_matches)
    d = player_diff_stats(player_matches).get(name,{"mean":0,"var":0,"std":0,"games":0,"stability":"不明"})
    recent = recent_result(player_matches)
    opp_stats = opponent_stats(player_matches, name)
    player_elo_sorted = sorted(player_elo_data.items(), key=lambda x: x[1], reverse=True)

    rank = None
    for i, (n, _) in enumerate(player_elo_sorted):
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
        d = d,
        recent=recent,
        opp_stats=opp_stats,
        elo=elo_value,
        rank=rank,
        trust=trust,
        history=player_history
    )



if __name__ == '__main__': # python app.pyって打ったときのみFlaskごと起動される（ほかのプログラムでこのプログラムの関数を使いまわしたいときにFlaskを起動させないおまじない）
    init_db() # アプリ始める前に、DBのテーブルあるか確認しとくわ「起動時に1回だけやるもの」と「アクセスのたびにやるもの」は別物！！！重要！！！
    app.run(debug=True) #debug=Trueは神機能①保存したら即反映②ブラウザの画面上に「何行目のどの計算で間違えたか」を、赤文字で詳しく表示してくれる③エラー画面の右側にあるボタンを押すと、ブラウザ上で直接「その時点での変数の値（例えば matches の中身など）」をのぞき見ることができる。
    # ！！注意！！本番環境（誰でも見れるように公開するときはdebug=Falseかこの一文消す！！コードの裏が見えたら危険！）