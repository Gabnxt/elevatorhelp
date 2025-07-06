import os
import pickle
import requests
from flask import Flask, render_template, request, jsonify
import dropbox
from dotenv import load_dotenv

# Initialize Flask app
app = Flask(__name__)

# Load environment variables
load_dotenv()
DROPBOX_REFRESH_TOKEN = os.getenv("DROPBOX_REFRESH_TOKEN")
DROPBOX_CLIENT_ID = os.getenv("DROPBOX_CLIENT_ID")
DROPBOX_CLIENT_SECRET = os.getenv("DROPBOX_CLIENT_SECRET")
DROPBOX_FOLDER = "/Master Elevator Prints"
CACHE_FILE = "dropbox_cache.pkl"

# Global variables
access_token = None
shared_link_cache = {}

# Token refresh logic
def refresh_access_token():
    global access_token
    response = requests.post("https://api.dropboxapi.com/oauth2/token", data={
        "grant_type": "refresh_token",
        "refresh_token": DROPBOX_REFRESH_TOKEN,
        "client_id": DROPBOX_CLIENT_ID,
        "client_secret": DROPBOX_CLIENT_SECRET
    })
    if response.status_code == 200:
        access_token = response.json()["access_token"]
    else:
        raise Exception("Failed to refresh access token: " + response.text)

refresh_access_token()

# Authenticate with Dropbox using refreshed token
dbx = dropbox.Dropbox(access_token)

# Function to generate direct Dropbox link
def get_direct_link(path):
    if path in shared_link_cache:
        return shared_link_cache[path]

    try:
        shared_link_metadata = dbx.sharing_create_shared_link_with_settings(path)
    except dropbox.exceptions.ApiError as e:
        if (isinstance(e.error, dropbox.sharing.CreateSharedLinkWithSettingsError) and 
            e.error.is_shared_link_already_exists()):
            links = dbx.sharing_list_shared_links(path=path, direct_only=True).links
            if links:
                shared_link_metadata = links[0]
            else:
                return None
        else:
            return None

    url = shared_link_metadata.url.replace("?dl=0", "?raw=1")
    shared_link_cache[path] = url
    return url

# Caching utilities
def is_cache_available():
    return os.path.exists(CACHE_FILE)

def load_cache():
    with open(CACHE_FILE, "rb") as f:
        return pickle.load(f)

def save_cache(data):
    with open(CACHE_FILE, "wb") as f:
        pickle.dump(data, f)

def scan_dropbox_folder():
    entries = []
    try:
        result = dbx.files_list_folder(DROPBOX_FOLDER, recursive=True)
        entries.extend(result.entries)
        while result.has_more:
            result = dbx.files_list_folder_continue(result.cursor)
            entries.extend(result.entries)
        save_cache(entries)
    except dropbox.exceptions.ApiError as e:
        print("Dropbox API error:", e)
    return entries

# Load Dropbox files
if is_cache_available():
    dropbox_files = load_cache()
else:
    dropbox_files = scan_dropbox_folder()

# Search indexing for faster results
search_index = {}
file_lookup = {}

for entry in dropbox_files:
    if isinstance(entry, dropbox.files.FileMetadata):
        file_lookup[entry.path_lower] = entry
        tokens = set(entry.name.lower().split())
        for token in tokens:
            if token not in search_index:
                search_index[token] = set()
            search_index[token].add(entry.path_lower)

# Grouping logic
def group_files_by_category(files):
    categories = {}
    for entry in files:
        if isinstance(entry, dropbox.files.FileMetadata):
            path_parts = entry.path_display.split("/")
            category = path_parts[2] if len(path_parts) >= 3 else "Uncategorized"
            direct_url = get_direct_link(entry.path_lower)
            if direct_url:
                entry_dict = {
                    'name': entry.name,
                    'path_display': entry.path_display,
                    'direct_url': direct_url
                }
                categories.setdefault(category, []).append(entry_dict)
    return categories

@app.route("/", methods=["GET"])
def index():
    query = request.args.get("query", "").strip().lower()
    filtered_files = []

    if query:
        keywords = query.split()
        sets = [search_index.get(k, set()) for k in keywords if k in search_index]
        if sets:
            matched_paths = set.intersection(*sets) if len(sets) > 1 else sets[0]
            filtered_files = [file_lookup[path] for path in matched_paths]
        else:
            filtered_files = []

    grouped_files = group_files_by_category(filtered_files) if query else {}
    return render_template("index.html", grouped_files=grouped_files, search=query)

@app.route("/refresh", methods=["POST"])
def refresh_cache():
    global dropbox_files
    dropbox_files = scan_dropbox_folder()
    return "Cache refreshed successfully."

# Rudimentary AI Q&A
@app.route("/ask", methods=["POST"])
def ask():
    question = request.json.get("question", "").lower()
    response = "I'm not sure how to answer that. Please try a different question."
    matches = []

    for token in question.split():
        matches.extend(search_index.get(token, []))

    unique_matches = list(set(matches))[:5]
    if unique_matches:
        links = [get_direct_link(path) for path in unique_matches if get_direct_link(path)]
        response = "Here are some documents that might help you:\n" + "\n".join(links)

    return jsonify({"answer": response})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
