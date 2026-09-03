import re
import os
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, time

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-change-this-in-production'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('DATABASE_PATH', os.path.join(BASE_DIR, 'bus_system.db'))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + DB_PATH.replace('\\', '/')
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- DATABASE MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='client')
    profile_pic = db.Column(db.String(200))

class BusRoute(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    departure = db.Column(db.String(100), nullable=False)
    destination = db.Column(db.String(100), nullable=False)
    bus_type = db.Column(db.String(50), nullable=False)
    departure_time = db.Column(db.String(50), nullable=False)
    departure_date = db.Column(db.String(50), default='Daily')
    price = db.Column(db.Integer, nullable=False)
    available_seats = db.Column(db.Integer, nullable=False)

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    route_id = db.Column(db.Integer, db.ForeignKey('bus_route.id'), nullable=False)
    seats = db.Column(db.String(100), nullable=False)
    booking_date = db.Column(db.DateTime, default=datetime.now)
    total_price = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='Confirmed')
    route = db.relationship('BusRoute', backref='bookings')
    user = db.relationship('User', backref='user_bookings')

class VehicleType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    seat_capacity = db.Column(db.Integer, nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Create tables and seed dummy data
with app.app_context():
    db.create_all()
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    inspector = inspect(db.engine)
    columns = [col['name'] for col in inspector.get_columns('user')]
    if 'profile_pic' not in columns:
        db.session.execute(text('ALTER TABLE user ADD COLUMN profile_pic VARCHAR(200)'))
        db.session.commit()
    booking_cols = [col['name'] for col in inspector.get_columns('booking')]
    if 'status' not in booking_cols:
        db.session.execute(text("ALTER TABLE booking ADD COLUMN status VARCHAR(20) DEFAULT 'Confirmed'"))
        db.session.commit()
        route_cols = [col['name'] for col in inspector.get_columns('bus_route')]
    route_cols = [col['name'] for col in inspector.get_columns('bus_route')]
    if 'departure_date' not in route_cols:
        db.session.execute(text("ALTER TABLE bus_route ADD COLUMN departure_date VARCHAR(50) DEFAULT 'Daily'"))
        db.session.commit()
    if BusRoute.query.count() == 0:
        dummy_routes = [
            BusRoute(departure='Lagos', destination='Abuja', bus_type='Big Bus (50-seater)', departure_time='08:00 AM', price=15000, available_seats=42),
            BusRoute(departure='Lagos', destination='Port Harcourt', bus_type='Normal Bus (18-seater)', departure_time='10:00 AM', price=12000, available_seats=10),
            BusRoute(departure='Abuja', destination='Kano', bus_type='Toyota Sienna (7-seater)', departure_time='09:00 AM', price=25000, available_seats=5),
            BusRoute(departure='Enugu', destination='Lagos', bus_type='Private Booking', departure_time='Flexible', price=150000, available_seats=1),
            BusRoute(departure='Ibadan', destination='Abuja', bus_type='Big Bus (50-seater)', departure_time='07:00 AM', price=14000, available_seats=50)
        ]
        # Create default admin account if none exists
        if not User.query.filter_by(role='admin').first():
            admin_user = User(
                username='admin',
                email='admin@swiftbus.com',
                password_hash=generate_password_hash('admin123', method='pbkdf2:sha256'),
                role='admin'
            )
        db.session.add(admin_user)
        db.session.commit()
        db.session.add_all(dummy_routes)
        db.session.commit()
    if VehicleType.query.count() == 0:
        db.session.add_all([
            VehicleType(name='Big Bus (50-seater)', seat_capacity=50),
            VehicleType(name='Normal Bus (18-seater)', seat_capacity=18),
            VehicleType(name='Toyota Sienna (7-seater)', seat_capacity=7),
            VehicleType(name='Private Booking', seat_capacity=1)
        ])
        db.session.commit()

# --- ROUTES ---

def trip_departed(route):
    """True if the trip's date & time have already passed."""
    try:
        d = datetime.strptime(route.departure_date, '%Y-%m-%d').date()
    except Exception:
        return False  # 'Daily' or empty → never expires
    t_str = (route.departure_time or '').strip().upper().replace(' ', '')
    depart_time = time(23, 59)
    for fmt in ('%I:%M%p', '%H:%M'):
        try:
            depart_time = datetime.strptime(t_str, fmt).time()
            break
        except Exception:
            continue
    return datetime.now() > datetime.combine(d, depart_time)

def check_password_strength(password):
    """Returns (True, '') if the password is strong, else (False, reason)."""
    if len(password) < 8:
        return False, 'Password must be at least 8 characters long.'
    if not re.search(r'[A-Z]', password):
        return False, 'Password must include at least one UPPERCASE letter.'
    if not re.search(r'[a-z]', password):
        return False, 'Password must include at least one lowercase letter.'
    if not re.search(r'\d', password):
        return False, 'Password must include at least one number.'
    return True, ''

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')

        strong, reason = check_password_strength(password)
        if not strong:
            flash(reason, 'danger')
            return redirect(url_for('signup'))

        if User.query.filter_by(email=email).first():
            flash('Email already registered. Please login.', 'danger')
            return redirect(url_for('login'))

        if User.query.filter_by(username=username).first():
            flash('Username is already taken. Please choose another.', 'danger')
            return redirect(url_for('signup'))

        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(username=username, email=email, password_hash=hashed_password, role='client')

        try:
            db.session.add(new_user)
            db.session.commit()
            flash('Account created successfully! Please login.', 'success')
            return redirect(url_for('login'))
        except IntegrityError:
            db.session.rollback()
            flash('That email or username is already registered. Please login or choose another.', 'danger')
            return redirect(url_for('signup'))

    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        remember = request.form.get('remember') == 'on'
        
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user, remember=remember)
            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password.', 'danger')
            
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    departure = request.args.get('departure')
    destination = request.args.get('destination')
    bus_type = request.args.get('bus_type')
    
    query = BusRoute.query
    
    if departure:
        query = query.filter_by(departure=departure)
    if destination:
        query = query.filter_by(destination=destination)
    if bus_type:
        query = query.filter_by(bus_type=bus_type)
        
    routes = query.all()
    next_booking = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.booking_date.desc()).first()
    return render_template('dashboard.html', user=current_user, routes=routes, next_booking=next_booking,
                           vehicle_types=VehicleType.query.all(), trip_departed=trip_departed)

@app.route('/book/<int:route_id>', methods=['GET', 'POST'])
@login_required
def book_route(route_id):
    route = BusRoute.query.get_or_404(route_id)
    
    # DYNAMIC SEAT COUNT
    vtype = VehicleType.query.filter_by(name=route.bus_type).first()
    if vtype:
        total_seats = vtype.seat_capacity
    else:
        numbers = re.findall(r'\d+', route.bus_type)
        total_seats = int(numbers[0]) if numbers else 20

    if request.method == 'POST' and trip_departed(route):
        flash('Sorry, this trip has already departed.', 'danger')
        return redirect(url_for('dashboard'))

    bookings = Booking.query.filter_by(route_id=route_id).all()
    booked_seats = []
    for b in bookings:
        booked_seats.extend(b.seats.split(','))
        
    if request.method == 'POST':
        selected_seats = request.form.getlist('seats')
        
        if not selected_seats:
            flash('Please select at least one seat.', 'danger')
            return redirect(url_for('book_route', route_id=route_id))
            
        for seat in selected_seats:
            if seat in booked_seats:
                flash(f'Sorry, seat {seat} was just booked.', 'danger')
                return redirect(url_for('book_route', route_id=route_id))
                
        total_price = len(selected_seats) * route.price
        new_booking = Booking(
            user_id=current_user.id,
            route_id=route_id,
            seats=','.join(selected_seats),
            total_price=total_price
        )
        db.session.add(new_booking)
        route.available_seats -= len(selected_seats)
        db.session.commit()
        
        flash(f'Success! Booked seats: {", ".join(selected_seats)}', 'success')
        return redirect(url_for('dashboard'))
        
    return render_template('booking.html', route=route, booked_seats=booked_seats, total_seats=total_seats)

@app.route('/my-bookings')
@login_required
def my_bookings():
    bookings = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.booking_date.desc()).all()
    return render_template('my_bookings.html', bookings=bookings, trip_departed=trip_departed)

@app.route('/cancel/<int:booking_id>', methods=['POST'])
@login_required
def cancel_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    # Security: only the owner can cancel their own ticket
    if booking.user_id != current_user.id:
        flash('You are not authorized to cancel this booking.', 'danger')
        return redirect(url_for('my_bookings'))
    ticket_code = 'SWB-{:04d}'.format(booking.id)
    route = BusRoute.query.get(booking.route_id)
    # Release the seats back to the bus
    route.available_seats += len(booking.seats.split(','))
    db.session.delete(booking)
    db.session.commit()
    flash(f'Ticket {ticket_code} cancelled. Seats released for other passengers.', 'info')
    return redirect(url_for('my_bookings'))
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        form_type = request.form.get('form_type')

        if form_type == 'update_info':
            new_username = request.form.get('username', '').strip()
            if not new_username:
                flash('Display name cannot be empty.', 'danger')
            else:
                existing = User.query.filter_by(username=new_username).first()
                if existing and existing.id != current_user.id:
                    flash('That display name is already taken.', 'danger')
                else:
                    current_user.username = new_username
                    db.session.commit()
                    flash('Profile updated successfully!', 'success')
            return redirect(url_for('profile'))

        elif form_type == 'change_password':
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')

            if not check_password_hash(current_user.password_hash, current_password):
                flash('Current password is incorrect.', 'danger')
            elif len(new_password) < 6:
                flash('New password must be at least 6 characters long.', 'danger')
            elif new_password != confirm_password:
                flash('New passwords do not match.', 'danger')
            else:
                current_user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
                db.session.commit()
                flash('Password changed successfully!', 'success')
            return redirect(url_for('profile'))

        elif form_type == 'upload_pic':
            file = request.files.get('profile_pic')
            if not file or file.filename == '':
                flash('No image selected.', 'danger')
            elif '.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS:
                ext = file.filename.rsplit('.', 1)[1].lower()
                filename = f'user_{current_user.id}_{int(datetime.now().timestamp())}.{ext}'
                if current_user.profile_pic:
                    old_path = os.path.join(app.config['UPLOAD_FOLDER'], current_user.profile_pic)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                current_user.profile_pic = filename
                db.session.commit()
                flash('Profile picture updated!', 'success')
            else:
                flash('Invalid file type. Use PNG, JPG, JPEG, or GIF.', 'danger')
            return redirect(url_for('profile'))
        
    bookings = Booking.query.filter_by(user_id=current_user.id).all()
    total_trips = len(bookings)
    total_seats = sum(len(b.seats.split(',')) for b in bookings)
    total_spent = sum(b.total_price for b in bookings)
    return render_template('profile.html', user=current_user, total_trips=total_trips,
                           total_seats=total_seats, total_spent=total_spent)

@app.route('/admin')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        flash('Access denied. Admins only.', 'danger')
        return redirect(url_for('dashboard'))
    routes = BusRoute.query.all()
    bookings = Booking.query.order_by(Booking.booking_date.desc()).all()
    total_revenue = sum(b.total_price for b in bookings)
    total_clients = User.query.filter_by(role='client').count()

    # Ticket verification lookup
    ticket_code = request.args.get('ticket', '').strip().upper()
    verified_booking = None
    if ticket_code:
        digits = ''.join(ch for ch in ticket_code if ch.isdigit())
        if digits:
            verified_booking = Booking.query.get(int(digits))

    return render_template('admin.html', user=current_user, routes=routes, bookings=bookings,
                           total_revenue=total_revenue, total_clients=total_clients,
                           verified_booking=verified_booking, ticket_code=ticket_code,
                           vehicle_types=VehicleType.query.all())

@app.route('/admin/confirm-boarding/<int:booking_id>', methods=['POST'])
@login_required
def confirm_boarding(booking_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    booking = Booking.query.get_or_404(booking_id)
    if booking.status == 'Boarded':
        flash('This ticket was already confirmed.', 'warning')
    else:
        booking.status = 'Boarded'
        db.session.commit()
        flash(f'Ticket SWB-{booking.id:04d} confirmed. Passenger cleared to board!', 'success')
    return redirect(url_for('admin_dashboard', ticket=f'SWB-{booking.id:04d}'))

@app.route('/admin/add-route', methods=['POST'])
@login_required
def add_route():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    price = int(request.form.get('price'))
    seats = int(request.form.get('available_seats'))
    if price <= 0 or seats <= 0:
        flash('Price and available seats must be greater than zero.', 'danger')
        return redirect(url_for('admin_dashboard'))
    new_route = BusRoute(
        departure=request.form.get('departure'),
        destination=request.form.get('destination'),
        bus_type=request.form.get('bus_type'),
        departure_time=request.form.get('departure_time'),
        departure_date=request.form.get('departure_date'),
        price=price,
        available_seats=seats
    )
    db.session.add(new_route)
    db.session.commit()
    flash('Route added successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/edit-route/<int:route_id>', methods=['GET', 'POST'])
@login_required
def edit_route(route_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    route = BusRoute.query.get_or_404(route_id)
    if request.method == 'POST':
        route.departure = request.form.get('departure')
        route.destination = request.form.get('destination')
        route.bus_type = request.form.get('bus_type')
        route.departure_time = request.form.get('departure_time')
        route.departure_date = request.form.get('departure_date')
        new_price = int(request.form.get('price'))
        new_seats = int(request.form.get('available_seats'))
        if new_price <= 0 or new_seats <= 0:
            flash('Price and available seats must be greater than zero.', 'danger')
            return redirect(url_for('edit_route', route_id=route_id))
        route.price = new_price
        route.available_seats = new_seats
        db.session.commit()
        flash('Route updated successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    return render_template('admin_edit.html', route=route, user=current_user,
                           vehicle_types=VehicleType.query.all())

@app.route('/admin/delete-route/<int:route_id>', methods=['POST'])
@login_required
def delete_route(route_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    Booking.query.filter_by(route_id=route_id).delete()
    route = BusRoute.query.get_or_404(route_id)
    db.session.delete(route)
    db.session.commit()
    flash('Route deleted.', 'info')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-vehicle-type', methods=['POST'])
@login_required
def add_vehicle_type():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    name = request.form.get('name', '').strip()
    capacity = request.form.get('seat_capacity')
    if not name or not capacity:
        flash('Please provide both a type name and seat capacity.', 'danger')
    elif VehicleType.query.filter_by(name=name).first():
        flash('That vehicle type already exists.', 'danger')
    elif int(capacity) <= 0:
        flash('Seat capacity must be greater than zero.', 'danger')
    else:
        db.session.add(VehicleType(name=name, seat_capacity=int(capacity)))
        db.session.commit()
        flash(f'Vehicle type "{name}" added!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-vehicle-type/<int:type_id>', methods=['POST'])
@login_required
def delete_vehicle_type(type_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    vtype = VehicleType.query.get_or_404(type_id)
    in_use = BusRoute.query.filter_by(bus_type=vtype.name).count()
    if in_use > 0:
        flash(f'Cannot delete "{vtype.name}" — {in_use} route(s) are currently using it.', 'danger')
    else:
        db.session.delete(vtype)
        db.session.commit()
        flash(f'Vehicle type "{vtype.name}" deleted.', 'info')
    return redirect(url_for('admin_dashboard'))

@app.route('/forgot-password')
def forgot_password():
    flash('Password reset is handled at the SwiftBus office with a valid ID.', 'info')
    return redirect(url_for('login'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

if __name__ == '__main__':
    app.run(debug=True)