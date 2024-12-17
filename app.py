from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_migrate import Migrate
from datetime import datetime, date, timezone
import nltk
from nltk.corpus import words
import ssl
import certifi
import json
import random
import os
from dotenv import load_dotenv
import logging
from sqlalchemy import func
from PyDictionary import PyDictionary
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ssl._create_default_https_context = ssl.create_default_context(cafile=certifi.where())
nltk_data_dir = os.path.join(os.getcwd(), 'nltk_data')
nltk.data.path.append(nltk_data_dir)
instance_path = os.path.join(os.getcwd(), 'instance')
os.makedirs(instance_path, exist_ok=True)

if os.getenv('FLASK_ENV') != 'production':
    load_dotenv()
database_url = os.getenv('DATABASE_URL')
if not database_url:
    raise RuntimeError("DATABASE_URL is not set. Please configure it in your environment variables.")

app = Flask(__name__, instance_path=instance_path)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# API KEY
MW_API_KEY = 'bff29416-af74-4873-bf21-fb2971ee7a56'
def load_blacklist(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return set(line.strip().lower() for line in f if line.strip())
    except FileNotFoundError:
        app.logger.error(f"Blacklist file not found: {filepath}")
        return set()
    except Exception as e:
        app.logger.error(f"Error loading blacklist: {e}")
        return set()
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
BLACKLIST_FILE = os.path.join(BASE_DIR, 'data', 'blacklisted_words.txt')
blacklisted_words = load_blacklist(BLACKLIST_FILE)

def contains_blacklisted_substring(text, blacklist):
    text_lower = text.lower()
    for word in blacklist:
        if word in text_lower:
            logger.info(f"Blacklisted word detected in '{text}': '{word}'")
            return True
    return False
word_list = set(word.lower() for word in words.words())
definition_cache = {}

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    contributions = db.Column(db.Text, nullable=True)
    date_joined = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    word_coins = db.Column(db.Integer, nullable=False, default=0)
    achievements = db.Column(db.Text, nullable=True, default="")
    words_entered_today = db.Column(db.Integer, nullable=False, default=0)
    last_word_entry_date = db.Column(db.Date, nullable=True)

    def is_community(self):
        return self.username == 'Community Acc'

class WordOfTheDay(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    word = db.Column(db.String(150), nullable=False)
    date = db.Column(db.Date, nullable=False, unique=True)

class Word(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    word = db.Column(db.String(150), unique=True, nullable=False)
    date_added = db.Column(db.DateTime, nullable=False, default=datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Word {self.word}>"

class FeatureRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    description = db.Column(db.Text, nullable=False)
    date_submitted = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('feature_requests', lazy=True))

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')

def check_and_award_achievements(user):
    achievements = user.achievements.split(',') if user.achievements else []
    contributions_count = len(user.contributions.split(',')) if user.contributions else 0

    new_achievements = []

    if contributions_count >= 1 and 'First Contribution' not in achievements:
        new_achievements.append({'name': 'First Contribution', 'image': url_for('static', filename='images/achievements/first_contribution.png')})

    if contributions_count >= 10 and '10 Contributions' not in achievements:
        new_achievements.append({'name': '10 Contributions', 'image': url_for('static', filename='images/achievements/ten_contributions.png')})

    if contributions_count >= 20 and '20 Contributions' not in achievements:
        new_achievements.append({'name': '20 Contributions', 'image': url_for('static', filename='images/achievements/twenty_contributions.png')})
    if new_achievements:
        achievements.extend([ach['name'] for ach in new_achievements])
        user.achievements = ','.join(achievements)
        db.session.commit()
        flash(f'New achievements unlocked: {", ".join([ach["name"] for ach in new_achievements])}', 'success')
    return new_achievements
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

import json

def is_valid_word(word):
    return word.lower() in word_list
dictionary = PyDictionary()

def get_word_definition(word):
    definition = dictionary.meaning(word)
    if not definition:
        return "Definition not found."
    return '; '.join([' | '.join(defs) for defs in definition.values()])

def get_word_of_the_day():
    today = date.today()
    word_of_the_day_entry = WordOfTheDay.query.filter_by(date=today).first()
    
    if word_of_the_day_entry:
        word = word_of_the_day_entry.word
    else:
        word_count = Word.query.count()
        if word_count == 0:
            logger.error("Word table is empty. Cannot select Word of the Day.")
            return None, None, None
        
        random_offset = random.randint(0, word_count - 1)
        word_entry = Word.query.offset(random_offset).first()
        if not word_entry:
            logger.error("Failed to select a random word.")
            return None, None, None
        
        new_word_of_the_day = WordOfTheDay(word=word_entry.word, date=today)
        db.session.add(new_word_of_the_day)
        db.session.commit()
        logger.info(f"Selected '{word_entry.word}' as Word of the Day for {today}.")
        word = word_entry.word
    
    user = User.query.filter(User.contributions.contains(word)).first()
    definition = get_word_definition(word)
    return word, user.username if user else "Unknown", definition

@app.route('/')
def index():
    users = User.query.all()
    leaderboard_data = sorted(
        users,
        key=lambda user: len(user.contributions.split(',')) if user.contributions else 0,
        reverse=True
    )
    word_of_the_day, discovered_by, definition = get_word_of_the_day()
    return render_template(
        'index.html',
        leaderboard=leaderboard_data,
        word_of_the_day=word_of_the_day,
        discovered_by=discovered_by,
        definition=definition
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.password == password:
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('Login Unsuccessful. Please check username and password.', 'login_error')
    return render_template('login.html')

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'logout_success')
    return redirect(url_for('index'))

@app.route('/profile')
@login_required
def profile():
    contributions = current_user.contributions.split(',') if current_user.contributions else []
    return render_template('profile.html', contributions=contributions)
@app.route('/shop')
@login_required
def shop():
    items = [
        {'name': 'Background Color', 'cost': 50, 'type': 'background_color'},
        {'name': 'Profile Badge', 'cost': 100, 'type': 'profile_badge'},
    ]
    return render_template('shop.html', items=items, word_coins=current_user.word_coins)

@app.route('/redeem', methods=['POST'])
@login_required
def redeem():
    item_type = request.form['item_type']
    item_cost = int(request.form['item_cost'])
    
    if current_user.word_coins >= item_cost:
        current_user.word_coins -= item_cost
        db.session.commit()
        flash('Item redeemed successfully!', 'success')
    else:
        flash('Not enough Word Coins.', 'danger')
    
    return redirect(url_for('shop'))

@app.route('/user/<int:user_id>')
def user_profile(user_id):
    user = User.query.get_or_404(user_id)
    contributions = user.contributions.split(',') if user.contributions else []
    return render_template('user_profile.html', user=user, contributions=contributions)

@app.route('/full_contributions/<int:user_id>')
@login_required
def full_contributions(user_id):
    user = User.query.get_or_404(user_id)
    contributions = user.contributions.split(',') if user.contributions else []
    return render_template('full_contributions.html', contributions=contributions, user=user)

@app.route('/word_game')
@login_required
def word_game():
    all_contributions = User.query.with_entities(User.contributions).all()
    words = set()
    for contribution in all_contributions:
        if contribution.contributions:
            words.update(contribution.contributions.split(','))
    selected_words = random.sample(words, min(len(words), 5))
    word_definitions = [(word, get_word_definition(word)) for word in selected_words]
    random.shuffle(word_definitions)
    return render_template('word_game.html', word_definitions=word_definitions)

@app.route('/search', methods=['GET', 'POST'])
def search():
    if request.method == 'POST':
        search_query = request.form['search_query']
        users = User.query.filter(User.username.contains(search_query)).all()
        return render_template('search_results.html', users=users, search_query=search_query)
    return render_template('search.html')

def is_word_in_contributions(word):
    users = User.query.filter(User.contributions.like(f"%{word}%")).all()
    return len(users) > 0

@app.route('/add_word', methods=['POST'])
def add_word():
    try:
        word = request.form['word'].strip().lower()

        if contains_blacklisted_substring(word, blacklisted_words):
            return jsonify({'status': 'error', 'message': 'The word contains prohibited content.'})
        
        if is_word_in_contributions(word):
            return jsonify({'status': 'error', 'message': 'Word already exists in the database.'})
        
        if not is_valid_word(word):
            return jsonify({'status': 'error', 'message': 'Invalid word.'})
        if current_user.is_authenticated:
            user = current_user
        else:
            user = User.query.filter_by(username='Community Acc').first()
            if not user:
                return jsonify({'status': 'error', 'message': 'Community account not found.'})
        if user.last_word_entry_date != date.today():
            user.words_entered_today = 0
            user.last_word_entry_date = date.today()
        if user.words_entered_today >= 100:
            account_type = 'your' if current_user.is_authenticated else 'Community account'
            return jsonify({'status': 'error', 'message': f'{account_type.capitalize()} has reached the daily limit for entering words.'})
        if user.contributions:
            user.contributions += f',{word}'
        else:
            user.contributions = word

        user.word_coins += 10
        user.words_entered_today += 1

        new_achievements = check_and_award_achievements(user)
        db.session.commit()
        response = {
            'status': 'success',
            'message': 'Word added to the database.'
        }

        if new_achievements:
            response['new_achievements'] = new_achievements

        return jsonify(response)

    except Exception as e:
        app.logger.error(f"Error in add_word function: {e}")
        return jsonify({'status': 'error', 'message': 'An error occurred while adding the word.'})

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        
        if contains_blacklisted_substring(username, blacklisted_words):
            flash('Username contains prohibited words or phrases.', 'signup_error')
            return render_template('signup.html')
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'signup_error')
            return render_template('signup.html')
        
        new_user = User(username=username, password=password)
        db.session.add(new_user)
        db.session.commit()
        
        flash('Account created successfully!', 'signup_success')
        return redirect(url_for('login'))
    
    return render_template('signup.html')

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        
        if username and contains_blacklisted_substring(username, blacklisted_words):
            flash('Invalid username.', 'settings_error')
            return render_template('settings.html')
        
        if username:
            current_user.username = username
        if password:
            current_user.password = password 
        
        db.session.commit()
        flash('Your profile has been updated!', 'settings_success')
        return render_template('settings.html')  
    
    return render_template('settings.html')

@app.route('/feature_request', methods=['GET', 'POST'])
@login_required
def feature_request():
    if request.method == 'POST':
        description = request.form['description'].strip()
        
        if not description:
            flash('Description is required.', 'danger')
            return redirect(url_for('index'))
        
        new_request = FeatureRequest(user_id=current_user.id, description=description)
        db.session.add(new_request)
        db.session.commit()
        
        flash('Your feature request has been submitted!', 'success')
        return redirect(url_for('index'))
    
    return render_template('feature_request.html')

def scheduled_word_selection():
    with app.app_context():
        get_word_of_the_day()

scheduler = BackgroundScheduler()
scheduler.add_job(func=scheduled_word_selection, trigger="cron", hour=0, minute=0)
scheduler.start()

import atexit
atexit.register(lambda: scheduler.shutdown())

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)