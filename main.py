from flask import Flask, render_template, request, redirect, url_for, session, flash
from pymongo import MongoClient
import bcrypt
import uuid
from send_email import send_verification_email
from constants import SECRET_KEY, MONGODB_URI

app = Flask(__name__)
app.secret_key = SECRET_KEY

# MongoDB connection
client = MongoClient(MONGODB_URI)
db = client["study-sessions"]
users_collection = db["users"]


# Home route
@app.route("/")
def home():
    if "email" in session:
        return render_template("welcome.html", email=session["email"])
    return render_template("index.html")


# Signup route
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        hashed_password = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())

        if users_collection.find_one({"email": email}):
            flash("Email already exists!")
            return redirect(url_for("signup"))

        verification_code = str(uuid.uuid4())  # Generate a unique verification code
        users_collection.insert_one(
            {
                "email": email,
                "password": hashed_password,
                "verified": False,
                "verification_code": verification_code,
            }
        )
        send_verification_email(email, verification_code, request.host_url)
        flash(
            "Signup successful! A verification email has been sent to your email address."
        )
        return redirect(url_for("login"))

    return render_template("signup.html")


# Verify email route
@app.route("/verify/<verification_code>")
def verify_email(verification_code):
    user = users_collection.find_one({"verification_code": verification_code})
    if user:
        users_collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"verified": True}, "$unset": {"verification_code": ""}},
        )
        flash("Email verified successfully! You can now log in.")
        return redirect(url_for("login"))
    else:
        flash("Invalid verification link.")
        return redirect(url_for("signup"))


# Login route
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        user = users_collection.find_one({"email": email})

        if user and bcrypt.checkpw(password.encode("utf-8"), user["password"]):
            if user.get("verified", False):
                session["email"] = email
                flash("Login successful!")
                return redirect(url_for("home"))
            else:
                flash("Email not verified! Please check your email.")
                return redirect(url_for("login"))

        flash("Invalid email or password!")
        return redirect(url_for("login"))

    if session.get("email", False):
        return redirect(url_for("home"))

    return render_template("login.html")


# Logout route
@app.route("/logout")
def logout():
    session.pop("email", None)
    flash("Logged out successfully!")
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)
