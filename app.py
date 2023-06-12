import os
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.utils import secure_filename
from gevent import monkey
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user
from flask import request, jsonify
import json
import logging


monkey.patch_all()
logging.basicConfig(level=logging.DEBUG)


# Create the extension
db = SQLAlchemy()

# Create the app
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
socketio = SocketIO(app, engineio_options={'socket_options': {'reuse_address': True}})
login_manager = LoginManager(app)

# Configure the SQLite database, relative to the app instance folder
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///C:/sqlite/messenger.db"
app.config['UPLOAD_FOLDER'] = 'venv/attachments'

# Initialize the app with the extension
db.init_app(app)

# Define the database models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    login = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    first_name = db.Column(db.String(30), nullable=False)
    last_name = db.Column(db.String(30), nullable=False)
    bio = db.Column(db.String(200))

    def __init__(self, login, password, first_name, last_name, bio=None):
        self.login = login
        self.password = password
        self.first_name = first_name
        self.last_name = last_name
        self.bio = bio

    def get_id(self):
        return str(self.id)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    chat_id = db.Column(db.Integer, db.ForeignKey('chat.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    sent_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __init__(self, user_id, chat_id, content, sent_at, attachment=None):
        self.user_id = user_id
        self.chat_id = chat_id
        self.content = content
        self.sent_at = sent_at

class Chat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(50), nullable=False)
    messages = db.relationship('Message', backref='chat', lazy=True)

    def __init__(self, title, messages=[]):
        self.title = title
        self.messages = messages


# Create the database tables
with app.app_context():
    db.create_all()

# Set up the login manager
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Routes for registration and login
@app.route('/login', methods=['GET', 'POST'])
def index():
    if current_user.is_authenticated:
        return redirect(url_for('chat'))

    if request.method == 'POST':
        login = request.form.get('login')
        password = request.form.get('password')

        log_user = User.query.filter_by(login=login).first()
        if log_user and log_user.password == password:
            login_user(log_user)
            return render_template('chat.html', user_id=current_user.get_id())
        else:
            return jsonify({'error': 'Invalid username or password'})

    return render_template('login.html', error=None)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('chat'))

    if request.method == 'POST':
        login = request.form.get('login')
        password = request.form.get('password')
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        bio = request.form.get('bio')

        existing_user = User.query.filter_by(login=login).first()
        if existing_user:
            return jsonify({'error': 'Username already exists'})

        try:
            new_user = User(login=login, password=password, first_name=first_name, last_name=last_name, bio=bio)
            db.session.add(new_user)
            db.session.commit()
        except Exception as e:
            app.logger.error("Ошибка доступа к базе данных: %s", str(e))

        login_user(new_user)

    return render_template('register.html', error=None)


@app.route('/chat')
def chat():
    if not current_user.is_authenticated:
        return redirect(url_for('index'))

    return render_template('chat.html', user_id=current_user.get_id())


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

# Route for creating a new chat
@app.route('/create_chat', methods=['POST'])
def create_chat():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Unauthorized'}), 401

    title = request.form.get('title')

    # Create a new Chat object and save it to the database
    chat = Chat(title=title)
    db.session.add(chat)
    db.session.commit()

    # Broadcast the new message to all connected clients in the chat room
    room = f'chat_{chat_id}'
    socketio.emit('message', {
        'id': message.id,
        'user_id': message.user_id,
        'content': message.content,
        'attachment': message.attachment,
        'sent_at': message.sent_at.isoformat()
    }, room=room)

    # Return the created chat as a JSON response
    return jsonify({
        'id': chat.id,
        'title': chat.title
    })

# Route for getting all chats
@app.route('/chats')
def get_chats():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Unauthorized'}), 401

    chats = Chat.query.all()
    return jsonify([{
        'id': chat.id,
        'title': chat.title
    } for chat in chats])


@app.route('/create_message', methods=['POST'])
def create_message():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Unauthorized'}), 401

    user_id = current_user.get_id()
    chat_id = request.form.get('chat_id')
    content = request.form.get('content')
    sent_at = datetime.now()

    # Create a new message
    message = Message(user_id=user_id, chat_id=chat_id, content=content, sent_at=sent_at)
    db.session.add(message)
    db.session.commit()  # Save the message to the database

    # Broadcast the new message to all connected clients in the chat room
    room = f'chat_{chat_id}'
    socketio.emit('message', {
        'id': message.id,
        'user_id': message.user_id,
        'content': message.content,
        'sent_at': message.sent_at.isoformat()
    }, room=room)

    return 'Message created successfully'

@app.route('/messages/<int:chat_id>')
def get_messages(chat_id):
    if not current_user.is_authenticated:
        return jsonify({'error': 'Unauthorized'}), 401

    messages = db.session.query(Message, User.login).join(User).filter(Message.chat_id == chat_id).all()
    return jsonify([{
        'id': message.id,
        'user_login': login,
        'content': message.content,
        'sent_at': message.sent_at.isoformat()
    } for message, login in messages])


if __name__ == '__main__':
    socketio.run(app)
