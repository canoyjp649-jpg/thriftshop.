from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
import sqlite3, os, time
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime

app = Flask(__name__)
app.secret_key = "secret123"

# ---------------- CONFIG ----------------
DB_NAME = "thrift.db"
UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ---------------- DB ----------------
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password_hash TEXT,
        role TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_id INTEGER,
        title TEXT,
        price REAL,
        condition_text TEXT,
        description TEXT,
        image_filename TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT,
        reviewed_at TEXT,
        reviewed_by INTEGER
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cart_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        item_id INTEGER,
        qty INTEGER DEFAULT 1
    )
    """)

    conn.commit()

    # seed users
    seed_user(conn, "admin", "admin123", "admin")
    seed_user(conn, "seller", "seller123", "seller")
    seed_user(conn, "buyer", "buyer123", "buyer")
    seed_user(conn, "jp", "jp123", "admin")

    conn.close()


def seed_user(conn, username, password, role):
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username=?", (username,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), role)
        )
        conn.commit()


# ---------------- HELPERS ----------------
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if session.get("role") not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ---------------- AUTH ----------------
@app.route("/")
def home():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username=?",
            (username,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            return redirect(url_for("shop"))

        flash("Invalid login")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------- SHOP ----------------
@app.route("/shop")
@login_required
def shop():
    conn = get_db()
    items = conn.execute("""
        SELECT items.*, users.username AS seller_name
        FROM items
        JOIN users ON users.id = items.seller_id
        WHERE items.status='approved'
        ORDER BY items.id DESC
    """).fetchall()
    conn.close()

    return render_template(
        "shop.html",
        items=items,
        user=session["username"],
        role=session["role"],
        cart_count=get_cart_count()
    )


# ---------------- SELL ----------------
@app.route("/sell", methods=["GET", "POST"])
@login_required
@role_required("seller", "admin")
def sell():
    if request.method == "POST":
        title = request.form["title"]
        price = float(request.form["price"])
        condition_text = request.form["condition_text"]
        description = request.form.get("description", "")

        files = request.files.getlist("images")  # FIXED MULTI IMAGE

        filenames = []

        for file in files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                filename = f"{int(time.time()*1000)}_{filename}"
                file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
                filenames.append(filename)

        image_str = ",".join(filenames)

        conn = get_db()
        conn.execute("""
            INSERT INTO items
            (seller_id, title, price, condition_text, description, image_filename, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (
            session["user_id"],
            title,
            price,
            condition_text,
            description,
            image_str,
            datetime.utcnow().isoformat()
        ))
        conn.commit()
        conn.close()

        flash("Item submitted for approval!")
        return redirect(url_for("shop"))

    return render_template("sell.html")


# ------------------------------------------------------ ADMIN -----------------------------------------------------------
@app.route("/admin")
@login_required
@role_required("admin")
def admin_review():
    conn = get_db()
    items = conn.execute("""
        SELECT items.*, users.username AS seller_name
        FROM items
        JOIN users ON users.id = items.seller_id
        ORDER BY items.id DESC
    """).fetchall()
    conn.close()

    return render_template("admin_review.html", items=items)


@app.route("/admin_review/action/<int:item_id>/<action>", methods=["POST"])
@login_required
@role_required("admin")
def admin_action(item_id, action):
    status = "approved" if action == "approve" else "rejected"

    conn = get_db()
    conn.execute("""
        UPDATE items
        SET status=?, reviewed_at=?, reviewed_by=?
        WHERE id=?
    """, (
        status,
        datetime.utcnow().isoformat(),
        session["user_id"],
        item_id
    ))
    conn.commit()
    conn.close()

    flash(f"Item {status}")
    return redirect(url_for("admin_review"))


@app.route("/admin/delete/<int:item_id>", methods=["POST"])
@login_required
@role_required("admin")
def admin_delete(item_id):
    conn = get_db()
    conn.execute("DELETE FROM cart_items WHERE item_id=?", (item_id,))
    conn.execute("DELETE FROM items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()

    flash("Item deleted")
    return redirect(url_for("admin_review"))


# ---------------- CART ----------------
def get_cart_count():
    if "user_id" not in session:
        return 0
    conn = get_db()
    count = conn.execute(
        "SELECT SUM(qty) as c FROM cart_items WHERE user_id=?",
        (session["user_id"],)
    ).fetchone()["c"]
    conn.close()
    return count or 0


@app.route("/cart")
@login_required
def cart():
    conn = get_db()

    rows = conn.execute("""
        SELECT cart_items.id AS cart_id, cart_items.qty,
               items.title, items.price, items.image_filename
        FROM cart_items
        JOIN items ON items.id = cart_items.item_id
        WHERE cart_items.user_id=?
    """, (session["user_id"],)).fetchall()

    total = sum(r["price"] * r["qty"] for r in rows)

    conn.close()

    return render_template("cart.html", rows=rows, total=total)


@app.route("/cart/add/<int:item_id>", methods=["POST"])
@login_required
def cart_add(item_id):
    conn = get_db()

    existing = conn.execute(
        "SELECT id FROM cart_items WHERE user_id=? AND item_id=?",
        (session["user_id"], item_id)
    ).fetchone()

    if existing:
        conn.execute("UPDATE cart_items SET qty=qty+1 WHERE id=?", (existing["id"],))
    else:
        conn.execute(
            "INSERT INTO cart_items (user_id, item_id, qty) VALUES (?, ?, 1)",
            (session["user_id"], item_id)
        )

    conn.commit()
    conn.close()

    return redirect(url_for("shop"))


@app.route("/cart/remove/<int:cart_id>", methods=["POST"])
@login_required
def cart_remove(cart_id):
    conn = get_db()
    conn.execute("DELETE FROM cart_items WHERE id=? AND user_id=?", (cart_id, session["user_id"]))
    conn.commit()
    conn.close()

    return redirect(url_for("cart"))


@app.route("/checkout", methods=["POST"])
@login_required
def checkout():
    conn = get_db()
    conn.execute("DELETE FROM cart_items WHERE user_id=?", (session["user_id"],))
    conn.commit()
    conn.close()

    flash("Checkout complete (demo only)")
    return redirect(url_for("shop"))


# ---------------- TERMS ----------------
@app.route("/terms")
def terms():
    return render_template("terms.html")


# ---------------- RUN ----------------
if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5001)

if __name__=="__main__":
    app.run(host="0.0.0.0", port=10000)