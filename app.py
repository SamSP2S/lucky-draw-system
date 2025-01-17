from flask import Flask, render_template, request, redirect, session, url_for, jsonify, send_file
import pymysql
import csv
from io import StringIO
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from datetime import datetime
import os
import tempfile
import random

app = Flask(__name__)
app.secret_key = 'Sp2s@2024!'  # Change this to a secure random value

# MySQL connection
db = pymysql.connect(
    host="localhost",
    port=3306,
    user="root",  # Replace with your MySQL username
    password="admin",  # Replace with your MySQL password
    database="lucky_draw"
)

# Rate limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["50 per minute"]
)

# Admin credentials
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "Sp2s@2024!"

UPLOAD_FOLDER = '/static/uploads/'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure the upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route("/")
def index():
    return render_template("index.html")
    
@app.route("/")
def home():
    return "Hello, World!"
    
def normalize_rates_with_unlucky(prizes):
    """Normalize prize rates including the unlucky rate from the prize inventory."""
    total_rate = sum(prize[3] for prize in prizes)  # Sum of all normalized rates including unlucky
    if total_rate == 0:
        return []  # Avoid division by zero

    normalized_prizes = []
    for prize in prizes:
        normalized_prizes.append((prize[0], prize[1], prize[2], prize[3] / total_rate if total_rate > 0 else 0))

    return normalized_prizes

@app.route("/validate_code", methods=["POST"])
@limiter.limit("10 per minute")  # Apply rate limiting to this route
def validate_code():
    try:
        data = request.json
        name, email, phone, code = data.get("name"), data.get("email"), data.get("phone"), data.get("code")

        with db.cursor() as cursor:
            # Check if the code exists in the prepared list
            cursor.execute("SELECT * FROM used_codes WHERE code = %s", (code,))
            existing_code = cursor.fetchone()
            if not existing_code:
                return jsonify({"status": "error", "message": "Invalid code!"}), 400

            # Check if the code has already been used
            if existing_code[2]:  # Assuming 'used' column is at index 2
                return jsonify({"status": "error", "message": "Code already used!"}), 400

            # Mark the code as used
            cursor.execute("UPDATE used_codes SET used = TRUE WHERE code = %s", (code,))
            db.commit()

            # Fetch prizes with their rates, including the unlucky rate from the database
            cursor.execute("SELECT id, prize_name, normalized_rate, quantity FROM prizes WHERE quantity > 0")
            prizes = cursor.fetchall()

            # Build weighted prize list using the fetched rates
            normalized_prizes = normalize_rates_with_unlucky(prizes)

            # Perform weighted random choice
            choice = random.choices(normalized_prizes, weights=[prize[3] for prize in normalized_prizes], k=1)[0]

            prize_id, prize_name, _, _ = choice

            # Handle unlucky case
            if prize_name == "Oh uh! Unlucky! Please try again next time.":
                cursor.execute(
                    "INSERT INTO customers (name, email, phone, code, prize) VALUES (%s, %s, %s, %s, %s)",
                    (name, email, phone, code, prize_name)
                )
                db.commit()
                return jsonify({"status": "unlucky", "message": prize_name})

            # Update prize quantity
            cursor.execute("UPDATE prizes SET quantity = quantity - 1 WHERE id = %s", (prize_id,))
            db.commit()

            # Save customer data
            cursor.execute(
                "INSERT INTO customers (name, email, phone, code, prize) VALUES (%s, %s, %s, %s, %s)",
                (name, email, phone, code, prize_name)
            )
            db.commit()

        return jsonify({"status": "success", "prize": prize_name})
    except Exception as e:
        print(f"Error validating code: {e}")
        return jsonify({"status": "error", "message": "An error occurred!"}), 500

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        else:
            return render_template("admin_login.html", error="Invalid credentials")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))

@app.route("/admin")
def admin_dashboard():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    page = int(request.args.get("page", 1))
    per_page = 10
    offset = (page - 1) * per_page

    search_query = request.args.get("search", "")
    sort_column = request.args.get("sort", "created_at")
    sort_order = request.args.get("order", "DESC")

    with db.cursor() as cursor:
        # Search and sort functionality
        search_sql = " AND (name LIKE %s OR email LIKE %s OR phone LIKE %s OR code LIKE %s)"
        search_values = (f"%{search_query}%", f"%{search_query}%", f"%{search_query}%", f"%{search_query}%")

        cursor.execute(f"""
            SELECT id, name, email, phone, code, prize, created_at
            FROM customers
            WHERE 1=1 {search_sql}
            ORDER BY {sort_column} {sort_order}
            LIMIT %s OFFSET %s
        """, (*search_values, per_page, offset))
        customers = cursor.fetchall()

        cursor.execute("SELECT id, prize_name, quantity, normalized_rate, image_path FROM prizes")
        prizes = cursor.fetchall()

        print("Prizes fetched from database:", prizes)  # Debugging statement

    return render_template(
        "admin.html",
        customers=customers,
        prizes=prizes,
    )
        
@app.route("/admin/customer/edit/<int:customer_id>", methods=["GET", "POST"])
def edit_customer(customer_id):
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        phone = request.form["phone"]
        with db.cursor() as cursor:
            cursor.execute(
                "UPDATE customers SET name = %s, email = %s, phone = %s WHERE id = %s",
                (name, email, phone, customer_id)
            )
            db.commit()
        return redirect(url_for("admin_dashboard"))
    with db.cursor() as cursor:
        cursor.execute("SELECT name, email, phone FROM customers WHERE id = %s", (customer_id,))
        customer = cursor.fetchone()
    return render_template("edit_customer.html", customer=customer, customer_id=customer_id)

@app.route("/admin/customer/delete/<int:customer_id>", methods=["POST"])
def delete_customer(customer_id):
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM customers WHERE id = %s", (customer_id,))
        db.commit()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/prize/edit/<int:prize_id>", methods=["GET", "POST"])
def edit_prize(prize_id):
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    # Fetch existing prize details
    with db.cursor() as cursor:
        cursor.execute("SELECT prize_name, quantity, normalized_rate, image_path FROM prizes WHERE id = %s", (prize_id,))
        prize = cursor.fetchone()

    if not prize:
        return "Prize not found", 404

    if request.method == "POST":
        prize_name = request.form["prize_name"]
        quantity = request.form["quantity"]
        normalized_rate = request.form["normalized_rate"]
        file = request.files["image"]

        # Handle file upload if new image is provided
        image_path = prize[3]  # Default to existing image
        if file and file.filename:
            filename = datetime.now().strftime("%Y%m%d%H%M%S_") + file.filename
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            image_path = filename

        # Update the prize in the database
        with db.cursor() as cursor:
            cursor.execute("""
                UPDATE prizes 
                SET prize_name = %s, quantity = %s, normalized_rate = %s, image_path = %s 
                WHERE id = %s
            """, (prize_name, quantity, normalized_rate, image_path, prize_id))
            db.commit()

        return redirect(url_for("admin_dashboard"))

    return render_template("edit_prize.html", prize=prize, prize_id=prize_id)

@app.route("/admin/add_prize", methods=["GET", "POST"])
def add_prize():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    
    if request.method == "POST":
        prize_name = request.form["prize_name"]
        quantity = request.form["quantity"]
        normalized_rate = request.form["normalized_rate"]
        file = request.files["image"]

        # Handle file upload
        if file and file.filename:
            # Generate a unique filename with a timestamp
            filename = datetime.now().strftime("%Y%m%d%H%M%S_") + file.filename
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            
            # Save the file to the uploads folder
            file.save(filepath)
            image_path = filename  # Save only the filename to the database
        else:
            image_path = None  # No image uploaded

        try:
            with db.cursor() as cursor:
                # Insert prize data into the database
                cursor.execute(
                    "INSERT INTO prizes (prize_name, quantity, normalized_rate, image_path) VALUES (%s, %s, %s, %s)",
                    (prize_name, quantity, normalized_rate, image_path)
                )
                db.commit()
            return redirect(url_for("admin_dashboard"))
        except Exception as e:
            print(f"Error adding prize: {e}")
            return redirect(url_for("admin_dashboard"))

    return render_template("add_prize.html")

@app.route("/admin/prize/delete/<int:prize_id>", methods=["POST"])
def delete_prize(prize_id):
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    try:
        with db.cursor() as cursor:
            cursor.execute("DELETE FROM prizes WHERE id = %s", (prize_id,))
            db.commit()
        return redirect(url_for("admin_dashboard"))
    except Exception as e:
        print(f"Error deleting prize: {e}")
        return redirect(url_for("admin_dashboard"))

@app.route("/admin/reset_codes", methods=["POST"])
def reset_codes():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    try:
        with db.cursor() as cursor:
            cursor.execute("UPDATE used_codes SET used = FALSE")
            db.commit()
        return redirect(url_for("admin_dashboard"))
    except Exception as e:
        print(f"Error resetting codes: {e}")
        return redirect(url_for("admin_dashboard"))

@app.route("/admin/reset_specific_code", methods=["POST"])
def reset_specific_code():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    code = request.form.get("code")
    if not code:
        return redirect(url_for("admin_dashboard"))
    try:
        with db.cursor() as cursor:
            cursor.execute("UPDATE used_codes SET used = FALSE WHERE code = %s", (code,))
            db.commit()
        return redirect(url_for("admin_dashboard"))
    except Exception as e:
        print(f"Error resetting specific code: {e}")
        return redirect(url_for("admin_dashboard"))

@app.route("/admin/export", methods=["GET"])
def export_data():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    try:
        # Create a temporary file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", mode="w", newline="")
        writer = csv.writer(temp_file)

        # Fetch customer data
        with db.cursor() as cursor:
            cursor.execute("SELECT name, email, phone, code, prize, created_at FROM customers")
            rows = cursor.fetchall()

        # Write headers and rows
        writer.writerow(["Name", "Email", "Phone", "Code", "Prize", "Entry Date"])
        if rows:
            writer.writerows(rows)
        else:
            writer.writerow(["No data available"])

        temp_file.close()
        return send_file(
            temp_file.name,
            mimetype="text/csv",
            as_attachment=True,
            download_name="customers.csv"
        )
    except Exception as e:
        print(f"Error exporting data: {e}")
        return "Error exporting data", 500

@app.route("/admin/backup", methods=["POST"])
def backup_database():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = f"backup_{timestamp}.sql"
        command = f"mysqldump -u root -padmin lucky_draw > {backup_file}"
        result = os.system(command)
        if result == 0:
            return send_file(backup_file, as_attachment=True)
        else:
            return "Error creating backup!", 500
    except Exception as e:
        print(f"Error creating backup: {e}")
        return str(e), 500
        
@app.route("/get_prizes", methods=["GET"])
def get_prizes():
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT prize_name FROM prizes WHERE quantity > 0")
            prizes = [row[0] for row in cursor.fetchall()]
        return jsonify({"status": "success", "prizes": prizes})
    except Exception as e:
        print(f"Error fetching prizes: {e}")
        return jsonify({"status": "error", "message": "Failed to fetch prizes."}), 500

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5001)))
