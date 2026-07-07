import sqlite3

DB_NAME = "matches.db"


def get_connection():
    return sqlite3.connect(DB_NAME)


def init_db(): # 起動時に一回だけテーブル作ってる、アクセスのたびに作ってるわけではない
    conn = get_connection() # 
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


# 試合データを読み込む関数、下のindex()で呼び出すために先に準備してる
# データベースからデータとってきてFlaskで使える形に変換する関数
def load_data():
    conn = get_connection() # 
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


def add_match(p1, p2, p3, p4, score1, score2):
    conn = sqlite3.connect("matches.db")
    c = conn.cursor()

    c.execute(
        """
        INSERT INTO matches(team1_p1, team1_p2, team2_p1, team2_p2, score1, score2)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (p1, p2, p3, p4, score1, score2),
    )

    conn.commit()
    conn.close()

def delete_match(id):
    conn = sqlite3.connect("matches.db")
    c = conn.cursor()

    c.execute("DELETE FROM matches WHERE id = ?", (id,))

    conn.commit()
    conn.close()