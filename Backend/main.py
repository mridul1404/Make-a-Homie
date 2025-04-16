# matchmaker_backend/main.py
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from typing import List
import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import OneHotEncoder, StandardScaler, FunctionTransformer
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import make_pipeline
from sklearn.metrics.pairwise import cosine_similarity
import os
import uuid
from datetime import datetime
import bcrypt

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # allow Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# === File paths ===
USER_FILE = "users.csv"
PRESENCE_FILE = "presence_log.csv"
MATCH_FILE = "matches.csv"
MESSAGE_FILE = "messages.csv"
CREDENTIALS_FILE = "credentials.csv"

# === Profile submission model ===
class UserProfile(BaseModel):
    name: str
    UserID: str
    age: int
    gender: str
    department: str
    preferred_study: str
    socialization_preference: str
    meeting_preference: str
    join_reason: str
    introvert_scale: float
    discussion_level: float
    combined_text: str
    mac: str = ""

class PresenceLog(BaseModel):
    mac: str
    timestamp: str
    location: str

class ChatMessage(BaseModel):
    sender_id: str
    receiver_id: str
    message: str

class UserCredentials(BaseModel):
    user_id: str
    password: str

# === Ensure data files exist ===
for file in [USER_FILE, PRESENCE_FILE, MATCH_FILE, MESSAGE_FILE, CREDENTIALS_FILE]:
    if not os.path.exists(file):
        pd.DataFrame().to_csv(file, index=False)

# === Helper: Load and preprocess data using TF-IDF model ===
def generate_matches(user_df):
    if user_df.empty:
        return {}

    text_col = "combined_text"
    categorical_cols = ["gender", "department", "preferred_study", "socialization_preference", "meeting_preference", "join_reason"]
    numeric_cols = ["age", "introvert_scale", "discussion_level"]

    text_weight = 3.0
    text_booster = make_pipeline(
        TfidfVectorizer(max_features=400),
        FunctionTransformer(lambda x: x * text_weight, accept_sparse=True)
    )

    preprocessor = ColumnTransformer([
        ("text", text_booster, text_col),
        ("cat", OneHotEncoder(sparse_output=False, handle_unknown="ignore"), categorical_cols),
        ("num", StandardScaler(), numeric_cols)
    ])

    user_vectors = preprocessor.fit_transform(user_df)
    similarity_matrix = cosine_similarity(user_vectors)
    np.fill_diagonal(similarity_matrix, 0)
    top_k = 5
    top_matches = np.argsort(-similarity_matrix, axis=1)[:, :top_k]

    matches = []
    for i, row in enumerate(top_matches):
        for j in row:
            score = similarity_matrix[i, j]
            if score > 0.5:
                matches.append((user_df.iloc[i]["UserID"], user_df.iloc[j]["UserID"], score))
    return matches

@app.post("/register")
def register_user(creds: UserCredentials):
    if os.path.exists(CREDENTIALS_FILE):
        df = pd.read_csv(CREDENTIALS_FILE)
        if creds.user_id in df["user_id"].values:
            raise HTTPException(status_code=400, detail="User ID already exists.")
    else:
        df = pd.DataFrame()

    hashed_pw = bcrypt.hashpw(creds.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    new_entry = pd.DataFrame([{"user_id": creds.user_id, "password": hashed_pw}])
    df = pd.concat([df, new_entry], ignore_index=True)
    df.to_csv(CREDENTIALS_FILE, index=False)
    return {"message": "User registered successfully."}

@app.post("/login")
def login_user(creds: UserCredentials):
    if not os.path.exists(CREDENTIALS_FILE):
        raise HTTPException(status_code=400, detail="No users registered yet.")

    df = pd.read_csv(CREDENTIALS_FILE)
    row = df[df.user_id == creds.user_id]
    if row.empty:
        raise HTTPException(status_code=401, detail="Invalid credentials.")

    stored_hash = row.iloc[0]["password"]
    if bcrypt.checkpw(creds.password.encode("utf-8"), stored_hash.encode("utf-8")):
        return {"message": "Login successful."}
    else:
        raise HTTPException(status_code=401, detail="Invalid credentials.")

@app.post("/submit_profile")
def submit_profile(profile: UserProfile):
    new_entry = profile.dict()
    if os.path.exists(USER_FILE):
        df = pd.read_csv(USER_FILE)
        if new_entry["UserID"] in df["UserID"].values:
            raise HTTPException(status_code=400, detail="UserID already exists. Please choose a unique one.")
        df = pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True)
    else:
        df = pd.DataFrame([new_entry])
    df.to_csv(USER_FILE, index=False)
    return {"message": "Profile submitted successfully."}

@app.post("/log_presence")
def log_presence(entry: PresenceLog):
    log = pd.read_csv(PRESENCE_FILE) if os.path.exists(PRESENCE_FILE) else pd.DataFrame()
    new_row = pd.DataFrame([{**entry.dict()}])
    log = pd.concat([log, new_row], ignore_index=True)
    log.to_csv(PRESENCE_FILE, index=False)
    return {"message": "Presence logged."}

@app.get("/match/{user_id}")
def get_matches(user_id: str):
    users_df = pd.read_csv(USER_FILE)
    presence_df = pd.read_csv(PRESENCE_FILE)
    if users_df.empty or presence_df.empty:
        return []

    user_mac = users_df[users_df.UserID == user_id]["mac"].values[0]
    if not user_mac:
        raise HTTPException(status_code=400, detail="User MAC not registered.")

    user_locations = presence_df[presence_df.mac == user_mac]["location"].unique()
    matched_users = presence_df[presence_df.location.isin(user_locations)]
    matched_macs = matched_users["mac"].unique()
    candidate_df = users_df[users_df["mac"].isin(matched_macs)]

    matches = generate_matches(candidate_df)
    result = []
    for u1, u2, score in matches:
        if u1 == user_id or u2 == user_id:
            other = u2 if u1 == user_id else u1
            result.append({"matched_with": other, "score": round(score, 3)})

    return result

@app.post("/chat/send")
def send_message(msg: ChatMessage):
    now = datetime.utcnow().isoformat()
    new_row = pd.DataFrame([{
        "sender_id": msg.sender_id,
        "receiver_id": msg.receiver_id,
        "message": msg.message,
        "timestamp": now
    }])
    if os.path.exists(MESSAGE_FILE):
        df = pd.read_csv(MESSAGE_FILE)
        df = pd.concat([df, new_row], ignore_index=True)
    else:
        df = new_row
    df.to_csv(MESSAGE_FILE, index=False)
    return {"message": "Message sent."}

@app.get("/chat/history/{user1}/{user2}")
def chat_history(user1: str, user2: str):
    if not os.path.exists(MESSAGE_FILE):
        return []
    df = pd.read_csv(MESSAGE_FILE)
    chat_df = df[((df.sender_id == user1) & (df.receiver_id == user2)) |
                 ((df.sender_id == user2) & (df.receiver_id == user1))]
    chat_df = chat_df.sort_values(by="timestamp")
    return chat_df.to_dict(orient="records")
