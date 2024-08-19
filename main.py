from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from pymongo import MongoClient
import bcrypt
import uuid
from send_email import send_verification_email
from constants import SECRET_KEY, MONGODB_URI
from flask_cors import CORS
from functools import wraps
import random
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_session import Session
import requests


app = Flask(__name__)
app.secret_key = SECRET_KEY

CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", transports=['websocket', 'polling'])

app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

# MongoDB connection
client = MongoClient(MONGODB_URI)
db = client["study-sessions"]
users_collection = db["users"]
sessions_collection = db["sessions"]

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "email" not in session:
            flash("You need to be logged in to access this page.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

# Home route
@app.route("/")
@login_required
def home():
    sessions = list(sessions_collection.find({"session_owner": session["email"]}, {"_id": 0}))
    return render_template("dashboard.html", email=session["email"], sessions=sessions)

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


@app.route("/create-session", methods=["POST"])
@login_required
def create_session():
    session_type = request.form.get("session-type")
    session_name = request.form.get("session-name")

    session_id = str(random.randint(100000, 999999))

    if session_type == "private":
        session_password = str(uuid.uuid4())
    else:
        session_password = None

    session_data = {
        "session_id": session_id,
        "session_type": session_type,
        "session_name": session_name,
        "session_password": session_password,
        "session_owner": session["email"],
        "participants": []
    }

    sessions_collection.insert_one(session_data)
    flash("Session created successfully!")

    return jsonify({"session_id": session_id, "session_password": session_password, "session_owner": session["email"], "session_name": session_name, "session_type": session_type})

@app.route("/sessions", methods=["POST"])
@login_required
def get_sessions():
    sessions = list(sessions_collection.find({"session_owner": session["email"]}, {"_id": 0}))
    return jsonify(sessions)


@app.route("/joined-sessions", methods=["POST"])
@login_required
def get_joined_sessions():
    sessions = list(sessions_collection.find({"participants": session["email"]}, {"_id": 0}))
    return jsonify(sessions)


@socketio.on("join")
def on_join(data):
    session_id = data["session_id"]
    email = session["email"]
    join_room(session_id)
    emit("user_joined", {"email": email}, to=session_id)

    # Emit the updated participants list to all clients in the session
    session_data = sessions_collection.find_one({"session_id": session_id})
    emit("update_participants", {"participants": session_data["participants"]}, to=session_id)

@socketio.on("leave")
def on_leave(data):
    session_id = data["session_id"]
    email = session["email"]
    leave_room(session_id)
    emit("user_left", {"email": email}, to=session_id)

    sessions_collection.update_one(
        {"session_id": session_id},
        {"$pull": {"participants": email}}
    )

    session_data = sessions_collection.find_one({"session_id": session_id}, {"_id": 0, "participants": 1})
    emit("update_participants", {"participants": session_data["participants"]}, to=session_id)


@socketio.on("send_message")
def handle_message(data):
    session_id = data["session_id"]
    message = data["message"]
    email = session["email"]

    # Save the message to the session document
    sessions_collection.update_one(
        {"session_id": session_id},
        {"$push": {"chat_messages": {"email": email, "message": message}}}
    )

    # Limit the chat messages to the latest 100
    sessions_collection.update_one(
        {"session_id": session_id},
        {"$push": {"chat_messages": {"$each": [], "$slice": -100}}}
    )

    emit("receive_message", {"email": email, "message": message}, to=session_id)


@app.route("/join-session/<session_id>", methods=["GET"])
@login_required
def join_session(session_id):
    session_data = sessions_collection.find_one({"session_id": session_id})

    if not session_data:
        return jsonify({"error": "Session not found!"}), 404

    if session_data["session_type"] == "private":
        if session_data["session_password"] != request.args.get("password"):
            return jsonify({"error": "Invalid password!"}), 403

    # Add the participant's email to the session document
    if session["email"] not in session_data["participants"]:
        sessions_collection.update_one(
            {"session_id": session_id},
            {"$push": {"participants": session["email"]}}
        )

    session_data.pop("_id")

    return render_template("study-session.html", session_data=session_data, email=session["email"])


@app.route("/leave-session/<session_id>", methods=["POST"])
@login_required
def leave_session(session_id):
    session_data = sessions_collection.find_one({"session_id": session_id})

    if not session_data:
        return jsonify({"error": "Session not found!"}), 200

    # if the user is the owner of the session, throw an error
    if session["email"] == session_data["session_owner"]:
        return jsonify({"error": "You are the owner of this session!"}), 403

    sessions_collection.update_one(
        {"session_id": session_id},
        {"$pull": {"participants": session["email"]}}
    )

    if request.form.get("json", False):
        return jsonify({"success": "Left session successfully!"})

    return redirect(url_for("home"))


@app.route("/end-session/<session_id>", methods=["POST"])
@login_required
def end_session(session_id):
    session_data = sessions_collection.find_one({"session_id": session_id})

    if not session_data:
        return jsonify({"error": "Session not found!"}), 404

    if session_data["session_owner"] != session["email"]:
        return jsonify({"error": "You are not the owner of this session!"}), 403

    # Notify all participants that the session is ending
    socketio.emit("end_session", {"session_id": session_id}, to=session_id)

    # Remove session from the database
    sessions_collection.delete_one({"session_id": session_id})

    if request.form.get("json", False):
        return jsonify({"success": "Session ended successfully!"})
    return redirect(url_for("home"))

@app.route("/upload-resource", methods=["POST"])
@login_required
def upload_resource():
    data = request.get_json()
    session_id = data.get("session_id")
    resource_link = data.get("link")
    resource_name = data.get("name")
    ext = data.get("ext")

    if not session_id or not resource_link or not ext:
        return jsonify({"error": "Invalid data"}), 400

    resource_id = str(uuid.uuid4())

    resource_data = {
        "resource_id": resource_id,
        "link": resource_link,
        "uploader": session["email"],
        "ext": ext,
        "name": resource_name
    }

    sessions_collection.update_one(
        {"session_id": session_id},
        {"$push": {"resources": resource_data}}
    )

    return jsonify({"success": True, "resource": resource_data})

@socketio.on("resource_uploaded")
def on_resource_uploaded(data):
    session_id = data["session_id"]
    resource = data["resource"]

    emit("update_resources", {"resource": resource}, to=session_id)

@socketio.on("load_video")
def on_load_video(data):
    session_id = data["session_id"]
    video_id = data["videoId"]
    current_time = data["currentTime"]
    is_playing = data["isPlaying"]

    emit("load_video", {
        "videoId": video_id,
        "currentTime": current_time,
        "isPlaying": is_playing
    }, to=session_id)

@socketio.on("video_control")
def on_video_control(data):
    session_id = data["session_id"]
    action = data["action"]
    current_time = data.get("currentTime", 0)

    emit("video_control", {
        "action": action,
        "currentTime": current_time
    }, to=session_id)


if __name__ == "__main__":
    socketio.run(app, debug=True)
