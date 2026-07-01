import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from pymongo import MongoClient
from datetime import datetime
from bson.objectid import ObjectId
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'super_secret_key'

basedir = os.path.abspath(os.path.dirname(__file__))

UPLOAD_FOLDER = os.path.join(basedir, 'static', 'uploads')

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

client = MongoClient('mongodb://localhost:27017/')
db = client['restaurant_db']

@app.route('/init')
def init_data():

    users = [
        {"login": "admin", "password": "123", "role": "admin", "name": "Головний Адмін", "status": "active"},
        {"login": "cook", "password": "123", "role": "cook", "name": "Шеф-Кухар", "status": "active"},
        {"login": "waiter", "password": "123", "role": "waiter", "name": "Офіціант Петро", "status": "active"}
    ]
    
    db.users.insert_many(users)
    
    return redirect(url_for('index'))

#ГОЛОВНА
@app.route('/')
def index():
    category_filter = request.args.get('category')
    if category_filter:
        dishes = list(db.dishes.find({"category": category_filter}))
    else:
        dishes = list(db.dishes.find())
    
    all_categories = db.dishes.distinct("category")
    
    #СОРТУВАННЯ
    settings = db.settings.find_one({"name": "menu_order"})
    if settings:
        custom_order = settings['list']
        all_categories.sort(key=lambda x: custom_order.index(x) if x in custom_order else 999)
    else:
        all_categories.sort() 

    return render_template('menu.html', 
                           dishes=dishes, 
                           categories=all_categories,
                           current_cat=category_filter, 
                           active_page='menu')

#ДОДАВАННЯ ВІДГУКУ
@app.route('/add_review', methods=['POST'])
def add_review():
    name = request.form.get('name')
    text = request.form.get('text')
    rating = int(request.form.get('rating'))
    
    db.reviews.insert_one({
        "name": name,
        "text": text,
        "rating": rating,
        "date": datetime.now()
    })
    return redirect(url_for('reviews_page'))

@app.route('/order', methods=['POST'])
def order():
    if not session.get('login'):
        flash("Щоб зробити замовлення, будь ласка, увійдіть або зареєструйтесь!", "error")
        return redirect(url_for('login'))

    dish_name = request.form.get('dish_name')
    dish_price = int(request.form.get('dish_price'))
    table_number = request.form.get('table_number')
    
    db.orders.insert_one({
        "table": table_number,
        "items": [{"name": dish_name, "price": dish_price}],
        "status": "pending", 
        "created_at": datetime.now(),
        "created_by": session['login']
    })

    flash("Замовлення прийнято! Дивіться статус у 'Мої замовлення'.", "success")
    return redirect(url_for('index'))

#ПАНЕЛЬ ОФІЦІАНТА
@app.route('/waiter')
def waiter_dashboard():
    if session.get('role') != 'waiter': return "Тільки для офіціантів!"
    
    orders = list(db.orders.find({
        "status": {"$in": ["pending", "new", "cooking", "ready", "served"]}
    }).sort([("table", 1), ("created_at", 1)]))
    
    occupied_tables = db.orders.find({"status": "served"}).distinct("table")
    
    return render_template('waiter.html', orders=orders, tables=occupied_tables)

@app.route('/serve_order/<order_id>')
def serve_order(order_id):
    db.orders.update_one({"_id": ObjectId(order_id)}, {"$set": {"status": "served"}})
    return redirect(url_for('waiter_dashboard'))

#ПЕРЕГЛЯД РАХУНКУ
@app.route('/bill/<table_number>')
def bill(table_number):
    if session.get('role') != 'waiter': return "Тільки для офіціантів!"

    table_orders = list(db.orders.find({
        "table": table_number,
        "status": {"$in": ["ready", "served"]} 
    }))
    
    total_sum = 0
    all_items = []
    
    for order in table_orders:
        for item in order['items']:
            all_items.append(item)
            total_sum += item['price']
            
    return render_template('bill.html', table=table_number, items=all_items, total=total_sum)

#ОПЛАТА
@app.route('/pay_table/<table_number>')
def pay_table(table_number):
    if session.get('role') != 'waiter': return "Тільки для офіціантів!"
    
    db.orders.update_many(
        {"table": table_number, "status": {"$in": ["ready", "served"]}},
        {"$set": {"status": "paid"}}
    )
    
    flash(f"Рахунок столу №{table_number} оплачено!", "success")
    return redirect(url_for('waiter_dashboard'))

@app.route('/confirm_order/<order_id>')
def confirm_order(order_id):

    db.orders.update_one({"_id": ObjectId(order_id)}, {"$set": {"status": "new"}})
    return redirect(url_for('waiter_dashboard'))

#СКАСУВАННЯ
@app.route('/cancel_order/<order_id>')
def cancel_order(order_id):
    db.orders.update_one({"_id": ObjectId(order_id)}, {"$set": {"status": "cancelled"}})
    return redirect(url_for('waiter_dashboard'))

@app.route('/close_order/<order_id>')
def close_order(order_id):
    db.orders.update_one({"_id": ObjectId(order_id)}, {"$set": {"status": "paid"}})
    return redirect(url_for('waiter_dashboard'))

#МОЇ ЗАМОВЛЕННЯ
@app.route('/my_orders')
def my_orders_page():
    if not session.get('login'):
        flash("Увійдіть, щоб переглянути історію.", "error")
        return redirect(url_for('login'))

    my_orders = list(db.orders.find({"created_by": session['login']}).sort("created_at", -1))
    
    return render_template('my_orders.html', orders=my_orders, active_page='my_orders')

#БАН КОРИСТУВАЧА
@app.route('/block_user/<user_id>')
def block_user(user_id):
    if session.get('role') != 'admin': return "Тільки адмін"
    
    user = db.users.find_one({"_id": ObjectId(user_id)})
    if not user: return "Користувача не знайдено"

    user_login = user['login']

    db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"is_blocked": True}})
    
    db.orders.delete_many({
        "created_by": user_login,
        "status": {"$ne": "paid"} 
    })
    
    db.reservations.delete_many({"created_by": user_login})

    flash(f"Користувача {user['name']} заблоковано.", "error")
    return redirect(url_for('admin_dashboard'))

#КУХНЯ
@app.route('/kitchen')
def kitchen():
    if session.get('role') != 'cook': return "Тільки для кухарів!"

    orders = list(db.orders.find({"status": {"$in": ["new", "cooking"]}})
                  .sort([("table", 1), ("created_at", 1)]))
    return render_template('kitchen.html', orders=orders, my_name=session.get('name'))

@app.route('/take_order/<order_id>')
def take_order(order_id):
    cook_name = session.get('name')
    db.orders.update_one(
        {"_id": ObjectId(order_id)}, 
        {"$set": {"status": "cooking", "cook_name": cook_name}}
    )
    return redirect(url_for('kitchen'))

@app.route('/ready/<order_id>')
def mark_ready(order_id):
    db.orders.update_one({"_id": ObjectId(order_id)}, {"$set": {"status": "ready"}})
    return redirect(url_for('kitchen'))

#ДОДАВАННЯ СТРАВ
@app.route('/add_dish', methods=['GET', 'POST'])
def add_dish():
    if session.get('role') != 'admin': return "Тільки адмін!"

    if request.method == 'POST':
        name = request.form.get('name')
        price = int(request.form.get('price'))
        category = request.form.get('category')
        
        img_path = "https://via.placeholder.com/150" 
        
        if 'image' in request.files:
            file = request.files['image']
            if file.filename != '':
                ext = os.path.splitext(file.filename)[1]
                filename = f"{uuid.uuid4().hex}{ext}"

                full_save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(full_save_path)
 
                img_path = "/static/uploads/" + filename

        db.dishes.insert_one({
            "name": name, 
            "price": price, 
            "category": category,
            "img": img_path
        })
        
        flash(f"Страву '{name}' успішно додано до меню!", "success")
        
        return redirect(url_for('index'))
    
    existing_categories = db.dishes.distinct("category")
    return render_template('add_dish.html', categories=existing_categories)

    existing_categories = db.dishes.distinct("category")
    return render_template('add_dish.html', categories=existing_categories)

@app.route('/delete_dish/<dish_id>')
def delete_dish(dish_id):
    if session.get('role') != 'admin': return "Тільки адмін!"
    dish = db.dishes.find_one({"_id": ObjectId(dish_id)})
    if dish and dish.get('img') and dish['img'].startswith('/static/uploads/'):
        try:
            os.remove(dish['img'].lstrip('/'))
        except: pass
    db.dishes.delete_one({"_id": ObjectId(dish_id)})
    return redirect(url_for('index'))

#АДМІНКА
@app.route('/admin')
def admin_dashboard():
    if session.get('role') != 'admin': return "Тільки адмін!"

    total = db.orders.count_documents({})
    pipeline = [{"$unwind": "$items"}, {"$group": {"_id": None, "total": {"$sum": "$items.price"}}}]
    res = list(db.orders.aggregate(pipeline))
    revenue = res[0]['total'] if res else 0
    
    view_type = request.args.get('view', 'staff')
    
    users_list = []
    
    if view_type == 'staff':
        users_list = list(db.users.find({"role": {"$ne": "client"}}))
        
        role_order = {'admin': 0, 'cook': 1, 'waiter': 2}

        users_list.sort(key=lambda u: role_order.get(u.get('role'), 99))
        
    elif view_type == 'clients':
        users_list = list(db.users.find({"role": "client"}).sort("name", 1))

    filter_type = request.args.get('filter')
    today_display = None 

    if filter_type == 'today':
        today_str = datetime.now().strftime('%Y-%m-%d')

        today_display = datetime.now().strftime('%d.%m.%Y')

        reservations = list(db.reservations.find({"date": today_str}).sort("time_start", 1))
    else:
        reservations = list(db.reservations.find().sort([("date", 1), ("time_start", 1)]))
    
    return render_template('admin.html', 
                           total=total, 
                           revenue=revenue, 
                           users=users_list,       
                           view_type=view_type,    
                           reservations=reservations, 
                           filter_type=filter_type,
                           today_display=today_display) 

@app.route('/delete_reservation/<res_id>')
def delete_reservation(res_id):
    if session.get('role') != 'admin': return "Тільки адмін!"
    
    db.reservations.delete_one({"_id": ObjectId(res_id)})
    return redirect(url_for('admin_dashboard'))

@app.route('/approve_user/<user_id>')
def approve_user(user_id):
    if session.get('role') != 'admin': return "Доступ заборонено"
    db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"status": "active"}})
    return redirect(url_for('admin_dashboard'))

@app.route('/delete_user/<user_id>')
def delete_user(user_id):
    if session.get('role') != 'admin': return "Доступ заборонено"
    db.users.delete_one({"_id": ObjectId(user_id)})
    return redirect(url_for('admin_dashboard'))

#ВХІД
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login = request.form.get('login')
        password = request.form.get('password')
        
        user = db.users.find_one({"login": login})
        
        if user:
            if user.get('is_blocked'):
                flash("Ваш акаунт заблоковано адміністратором через підозрілу активність!", "error")
                return redirect(url_for('login'))

            if user['password'] == password:
                db.users.update_one({"_id": user['_id']}, {"$set": {"failed_attempts": 0}})
                
                session['name'] = user['name']
                session['role'] = user['role']
                session['login'] = user['login'] 
                return redirect(url_for('index'))
            else:
                attempts = user.get('failed_attempts', 0) + 1
                db.users.update_one({"_id": user['_id']}, {"$set": {"failed_attempts": attempts}})
                
                if attempts >= 3:
                    db.users.update_one({"_id": user['_id']}, {"$set": {"is_blocked": True}})
                    flash("Акаунт заблоковано через 3 невірні спроби пароля! Зверніться до адміна.", "error")
                else:
                    flash(f"Невірний пароль! Залишилось спроб: {3 - attempts}", "error")
                    
        else:
            flash("Користувача не знайдено", "error")
            
    return render_template('login.html')

#РЕЄСТРАЦІЯ
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        login_email = request.form.get('login')
        password = request.form.get('password')
        role = request.form.get('role')

        if db.users.find_one({"login": login_email}):
            flash("Такий логін вже зайнятий!", "error")
            return redirect(url_for('register'))

        if role == 'client':
            user_status = 'active'
        else:
            user_status = 'pending'

        db.users.insert_one({
            "name": name,
            "login": login_email,
            "password": password,
            "role": role,
            "status": user_status,
            "failed_attempts": 0,
            "is_blocked": False,
            "created_at": datetime.now()
        })
        
        if role == 'client':
            session['name'] = name
            session['role'] = role
            session['login'] = login_email
            
            flash(f"Вітаємо, {name}! Реєстрація успішна.", "success")
            return redirect(url_for('index'))
            
        else:
            return redirect(url_for('pending_page'))

    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/fix_statuses')
def fix_statuses():
    db.users.update_many(
        {"status": {"$exists": False}}, 
        {"$set": {"status": "active"}}
    )
    return "Статуси виправлено! Всі старі користувачі тепер Активні. <a href='/admin'>В адмінку</a>"

#БРОНЮВАННЯ СТОЛИКА
@app.route('/book_table', methods=['POST'])
def book_table():
    if not session.get('login'):
        flash("Бронювання доступне тільки для авторизованих користувачів!", "error")
        return redirect(url_for('login'))

    name = session.get('name') 
    phone = request.form.get('phone')
    date_str = request.form.get('date')
    time_start = request.form.get('time_start')
    time_end = request.form.get('time_end')
    guests = request.form.get('guests')

    booking_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    today_date = datetime.now().date()
    
    if booking_date < today_date:
        flash("Не можна бронювати столик на минуле!", "error")
        return redirect(url_for('booking_page'))

    if time_start >= time_end:
        flash("Час початку має бути раніше за час завершення!", "error")
        return redirect(url_for('booking_page'))

    MAX_TABLES = 12
    overlapping_reservations = db.reservations.count_documents({
        "date": date_str,
        "$and": [
            {"time_start": {"$lt": time_end}},
            {"time_end": {"$gt": time_start}}
        ]
    })

    if overlapping_reservations >= MAX_TABLES:
        flash(f"На жаль, на час {time_start}-{time_end} все зайнято.", "error")
        return redirect(url_for('booking_page'))

    db.reservations.insert_one({
        "name": name,          
        "phone": phone,
        "date": date_str,
        "time_start": time_start,
        "time_end": time_end,
        "guests": guests,
        "created_at": datetime.now(),
        "created_by": session['login']
    })
    
    flash("Успішно! Чекаємо на вас.", "success")
    return redirect(url_for('booking_page'))

#СТОРІНКА БРОНЮВАННЯ
@app.route('/booking')
def booking_page():
    return render_template('booking.html', active_page='booking')

#СТОРІНКА ВІДГУКІВ
@app.route('/reviews')
def reviews_page():
    reviews = list(db.reviews.find().sort("date", -1))
    return render_template('reviews.html', reviews=reviews, active_page='reviews')

@app.route('/pending')
def pending_page():
    return render_template('pending.html')

#РЕДАГУВАННЯ КОРИСТУВАЧА
@app.route('/edit_user/<user_id>', methods=['GET', 'POST'])
def edit_user(user_id):
    if session.get('role') != 'admin': return "Тільки адмін!"

    user = db.users.find_one({"_id": ObjectId(user_id)})
    
    if request.method == 'POST':
        new_name = request.form.get('name')
        new_login = request.form.get('login')
        
        db.users.update_one({"_id": ObjectId(user_id)}, {
            "$set": {"name": new_name, "login": new_login}
        })
        
        flash(f"Дані користувача {new_name} оновлено!", "success")
        return redirect(url_for('admin_dashboard', view='staff'))
        
    return render_template('edit_user.html', user=user)

#НАЛАШТУВАННЯ (Сортування меню)
@app.route('/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    if session.get('role') != 'admin': return "Тільки адмін!"

    if request.method == 'POST':
        data = request.get_json()
        new_order = data.get('order') 
        
        if new_order:
            db.settings.update_one(
                {"name": "menu_order"}, 
                {"$set": {"list": new_order}}, 
                upsert=True
            )
            return jsonify({"status": "success"})
        return jsonify({"status": "error"}), 400

    real_categories = db.dishes.distinct("category")

    settings = db.settings.find_one({"name": "menu_order"})
    saved_order = settings['list'] if settings else []
    
    final_list = [cat for cat in saved_order if cat in real_categories]

    for cat in real_categories:
        if cat not in final_list:
            final_list.append(cat)
    
    return render_template('admin_settings.html', categories=final_list)

if __name__ == '__main__':
    app.run(debug=True)