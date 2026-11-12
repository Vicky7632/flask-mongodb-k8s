import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from werkzeug.exceptions import BadRequest

app = Flask(__name__)

mongo_client = MongoClient(
    os.environ.get("MONGODB_URI", "mongodb://localhost:27017"),
    serverSelectionTimeoutMS=5000,
)
collection = mongo_client[
    os.environ.get("MONGODB_DATABASE", "flask_mongodb_k8s")
][os.environ.get("MONGODB_COLLECTION", "data")]


@app.get("/")
def welcome():
    return jsonify(
        message="Welcome to the Flask and MongoDB API",
        utc_time=datetime.now(timezone.utc).isoformat(),
    )


@app.post("/data")
def create_data():
    if not request.is_json:
        return jsonify(error="Request body must be valid JSON."), 400

    try:
        document = request.get_json()
    except BadRequest:
        return jsonify(error="Request body must be valid JSON."), 400

    if not isinstance(document, dict):
        return jsonify(error="Request body must be a JSON object."), 400

    try:
        result = collection.insert_one(document)
    except PyMongoError:
        app.logger.error("MongoDB insert failed.")
        return jsonify(error="The database operation failed."), 503

    return jsonify(message="Data inserted successfully.", id=str(result.inserted_id)), 201


@app.get("/data")
def get_data():
    try:
        documents = list(collection.find())
    except PyMongoError:
        app.logger.error("MongoDB query failed.")
        return jsonify(error="The database operation failed."), 503

    for document in documents:
        document["_id"] = str(document["_id"])

    return jsonify(documents)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
