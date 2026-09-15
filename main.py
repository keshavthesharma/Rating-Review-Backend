from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import sqlite3
import hashlib
import secrets
import urllib.parse
import sys

def init_db(reset=False):
    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()
    
    if reset:
        cursor.execute("DROP TABLE IF EXISTS reviews")
        cursor.execute("DROP TABLE IF EXISTS items")
        cursor.execute("DROP TABLE IF EXISTS users")
        conn.commit()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            token TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            comment TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (item_id) REFERENCES items (id)
        )
    """)
    
    conn.commit()
    conn.close()

class BackendHandler(BaseHTTPRequestHandler):

    def send_json(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def parse_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def get_authenticated_user(self):
        auth_header = self.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None
        token = auth_header.split(" ")[1]
        
        conn = sqlite3.connect("app.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, username FROM users WHERE token = ?", (token,))
        user = cursor.fetchone()
        conn.close()
        return user

    def do_POST(self):
        path = self.path
        data = self.parse_body()
        conn = sqlite3.connect("app.db")
        cursor = conn.cursor()

        if path == "/register":
            username = data.get("username")
            password = data.get("password")
            if not username or not password:
                self.send_json(400, {"error": "Username and password required"})
                return

            pwd_hash = hashlib.sha256(password.encode()).hexdigest()
            try:
                cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, pwd_hash))
                conn.commit()
                self.send_json(201, {"message": "User registered successfully"})
            except sqlite3.IntegrityError:
                self.send_json(400, {"error": "Username already exists"})
            finally:
                conn.close()

        elif path == "/login":
            username = data.get("username")
            password = data.get("password")
            pwd_hash = hashlib.sha256(password.encode()).hexdigest()

            cursor.execute("SELECT id FROM users WHERE username = ? AND password_hash = ?", (username, pwd_hash))
            user = cursor.fetchone()
            if user:
                token = secrets.token_hex(16)
                cursor.execute("UPDATE users SET token = ? WHERE id = ?", (token, user[0]))
                conn.commit()
                self.send_json(200, {"message": "Login successful", "token": token})
            else:
                self.send_json(401, {"error": "Invalid credentials"})
            conn.close()

        elif path == "/items":
            name = data.get("name")
            desc = data.get("description", "")
            if not name:
                self.send_json(400, {"error": "Item name is required"})
                return

            cursor.execute("INSERT INTO items (name, description) VALUES (?, ?)", (name, desc))
            conn.commit()
            item_id = cursor.lastrowid
            conn.close()
            self.send_json(201, {"message": "Item added", "item_id": item_id})

        elif path == "/reviews":
            user = self.get_authenticated_user()
            if not user:
                self.send_json(401, {"error": "Unauthorized. Header 'Authorization: Bearer <token>' required."})
                conn.close()
                return

            item_id = data.get("item_id")
            rating = data.get("rating")
            comment = data.get("comment") or data.get("review_text", "")

            if not item_id or not rating or not (1 <= int(rating) <= 5):
                self.send_json(400, {"error": "Valid item_id and rating (1-5) required"})
                conn.close()
                return

            cursor.execute("INSERT INTO reviews (user_id, item_id, rating, comment) VALUES (?, ?, ?, ?)",
                           (user[0], item_id, rating, comment))
            conn.commit()
            conn.close()
            self.send_json(201, {"message": "Review submitted successfully"})

        else:
            conn.close()
            self.send_json(404, {"error": "Endpoint not found"})

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        conn = sqlite3.connect("app.db")
        cursor = conn.cursor()

        # ALL-IN-ONE ENDPOINT
        if path == "/dashboard" or path == "/all" or path == "/":
            cursor.execute("""
                SELECT 
                    items.id, 
                    items.name, 
                    items.description, 
                    ROUND(COALESCE(AVG(reviews.rating), 0), 2) AS average_rating
                FROM items
                LEFT JOIN reviews ON items.id = reviews.item_id
                GROUP BY items.id
            """)
            items_rows = cursor.fetchall()
            
            dashboard_data = []
            for item in items_rows:
                item_id = item[0]
                cursor.execute("""
                    SELECT r.id, u.username, r.rating, r.comment 
                    FROM reviews r 
                    JOIN users u ON r.user_id = u.id 
                    WHERE r.item_id = ?
                """, (item_id,))
                reviews = [{"id": r[0], "username": r[1], "rating": r[2], "comment": r[3]} for r in cursor.fetchall()]
                
                dashboard_data.append({
                    "item_id": item_id,
                    "item_name": item[1],
                    "description": item[2],
                    "average_rating": item[3],
                    "total_reviews": len(reviews),
                    "reviews": reviews
                })
            
            conn.close()
            self.send_json(200, dashboard_data)

        elif path == "/items":
            cursor.execute("""
                SELECT 
                    items.id, 
                    items.name, 
                    items.description, 
                    ROUND(COALESCE(AVG(reviews.rating), 0), 2) AS average_rating
                FROM items
                LEFT JOIN reviews ON items.id = reviews.item_id
                GROUP BY items.id
            """)
            items = [
                {
                    "id": r[0], 
                    "name": r[1], 
                    "description": r[2], 
                    "average_rating": r[3]
                } 
                for r in cursor.fetchall()
            ]
            conn.close()
            self.send_json(200, items)

        elif path == "/reviews":
            item_id = params.get("item_id", [None])[0]
            if not item_id:
                self.send_json(400, {"error": "Query parameter 'item_id' is required"})
                conn.close()
                return

            cursor.execute("SELECT name FROM items WHERE id = ?", (item_id,))
            item_row = cursor.fetchone()
            item_name = item_row[0] if item_row else "Unknown Item"

            cursor.execute("""
                SELECT r.id, u.username, r.rating, r.comment 
                FROM reviews r 
                JOIN users u ON r.user_id = u.id 
                WHERE r.item_id = ?
            """, (item_id,))
            reviews = [{"id": r[0], "username": r[1], "rating": r[2], "comment": r[3]} for r in cursor.fetchall()]

            cursor.execute("SELECT AVG(rating), COUNT(rating) FROM reviews WHERE item_id = ?", (item_id,))
            avg_res, count_res = cursor.fetchone()

            conn.close()
            self.send_json(200, {
                "item_id": int(item_id),
                "item_name": item_name,
                "average_rating": round(avg_res, 2) if avg_res else 0.0,
                "total_reviews": count_res,
                "reviews": reviews
            })
        else:
            conn.close()
            self.send_json(404, {"error": "Endpoint not found"})

if __name__ == "__main__":
    should_reset = "--reset" in sys.argv
    init_db(reset=should_reset)
    if should_reset:
        print("Database cleanly reset!")
    server = HTTPServer(("localhost", 8000), BackendHandler)
    print("Standard Library Server running on http://localhost:8000 ...")
    server.serve_forever()
