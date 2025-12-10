from flask import Flask, render_template, request, redirect, url_for, session, jsonify, make_response
import json, os
from datetime import datetime
from fpdf import FPDF
from collections import Counter
from arabic_reshaper import reshape
from bidi.algorithm import get_display
import secrets
import string

app = Flask(__name__)
app.secret_key = "secret123"

# Resolve data paths relative to this file so the app works no matter the cwd
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def resolve_path(path):
    if os.path.isabs(path):
        return path
    return os.path.join(BASE_DIR, path)

PRODUCTS_FILE = resolve_path("products.json")
ORDERS_FILE = resolve_path("orders.json")
IP_RATE_LIMIT_FILE = resolve_path("ip_rate_limit.json")

# Arabic font path
AMIRI_FONT = resolve_path(os.path.join("static", "fonts", "Amiri-Regular.ttf"))

def load_data(file_path):
    full_path = resolve_path(file_path)
    if os.path.exists(full_path):
        with open(full_path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except:
                return {}
    return {}

def save_data(file_path, data):
    full_path = resolve_path(file_path)
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def generate_order_id():
    """Generate a random, secure order ID."""
    # Generate 8 random alphanumeric characters (uppercase letters and digits)
    random_part = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    return f"ORD{random_part}"

def ensure_purchase_price(batch):
    """Guarantee a purchase_price key exists on a batch."""
    if "purchase_price" not in batch:
        batch["purchase_price"] = float(batch.get("price", 0))
    return batch

def rtl(text):
    if not text:
        return ""
    reshaped = reshape(text)
    return get_display(reshaped)

def get_client_ip():
    """Get the client's IP address, handling proxies."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr

def can_place_order(ip_address):
    """Check if an IP address can place an order (one per hour)."""
    ip_data = load_data(IP_RATE_LIMIT_FILE) or {}
    
    if ip_address not in ip_data:
        return True, None
    
    last_order_time_str = ip_data[ip_address].get("last_order_time")
    if not last_order_time_str:
        return True, None
    
    try:
        last_order_time = datetime.strptime(last_order_time_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        time_diff = (now - last_order_time).total_seconds() / 3600  # Convert to hours
        
        if time_diff >= 1.0:
            return True, None
        else:
            remaining_minutes = int((1.0 - time_diff) * 60)
            return False, remaining_minutes
    except:
        return True, None

def record_order_ip(ip_address):
    """Record that an IP address has placed an order."""
    ip_data = load_data(IP_RATE_LIMIT_FILE) or {}
    ip_data[ip_address] = {
        "last_order_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_data(IP_RATE_LIMIT_FILE, ip_data)

@app.route('/invoice/<order_id>')
def invoice(order_id):
    orders = load_data(ORDERS_FILE) or []
    settings = load_data("pharmacy.json") or {}

    order = next((o for o in orders if o["order_id"] == order_id), None)
    if not order:
        return "❌ الطلب غير موجود", 404

    if order["status"] != "مكتمل":
        return "⚠️ الفاتورة متاحة فقط للطلبات المكتملة", 403

    # إعداد PDF
    pdf = FPDF()
    pdf.add_page()

    # إضافة خط عربي
    try:
        pdf.add_font("Amiri", "", AMIRI_FONT, uni=True)
    except RuntimeError:
        return f"❌ ملف الخط Amiri-Regular.ttf غير موجود. ضع الملف هنا: {AMIRI_FONT}", 500

    pdf.set_font("Amiri", "", 16)

    # -------------------------------
    #   رأس الفاتورة
    # -------------------------------
    pdf.set_font("Amiri", "", 20)
    pdf.cell(0, 10, rtl(settings.get("name", "")), ln=True, align="R")

    pdf.set_font("Amiri", "", 12)
    pdf.cell(0, 8, rtl(f"العنوان: {settings.get('address','')}"), ln=True, align="R")
    pdf.cell(0, 8, rtl(f"هاتف: {settings.get('phone','')}"), ln=True, align="R")
    pdf.cell(0, 8, rtl(f"ترخيص مهني: {settings.get('license','')}"), ln=True, align="R")
    pdf.cell(0, 8, rtl(f"الرقم الضريبي: {settings.get('tax_number','')}"), ln=True, align="R")

    pdf.ln(5)
    pdf.set_font("Amiri", "", 16)
    pdf.cell(0, 10, rtl("فاتورة بيع"), ln=True, align="C")

    pdf.ln(5)
    pdf.set_font("Amiri", "", 14)

    # -------------------------------
    #   تفاصيل الفاتورة
    # -------------------------------
    pdf.cell(0, 8, rtl(f"رقم الفاتورة: {order_id}"), ln=True, align="R")
    pdf.cell(0, 8, rtl(f"العميل: {order['name']}"), ln=True, align="R")
    pdf.cell(0, 8, rtl(f"الهاتف: {order['phone']}"), ln=True, align="R")
    pdf.cell(0, 8, rtl(f"التاريخ: {datetime.now().strftime('%Y-%m-%d %H:%M')}"), ln=True, align="R")

    pdf.ln(5)
    pdf.cell(0, 8, rtl("-----------------------------------------"), ln=True, align="C")

    # -------------------------------
    #   جدول العناصر
    # -------------------------------
    pdf.set_font("Amiri", "", 14)
    pdf.cell(60, 8, rtl("الإجمالي"), border=1, align="C")
    pdf.cell(40, 8, rtl("السعر"), border=1, align="C")
    pdf.cell(30, 8, rtl("الكمية"), border=1, align="C")
    pdf.cell(60, 8, rtl("المنتج"), border=1, ln=True, align="C")

    total = 0
    for pid, item in order["items"].items():
        qty = int(item["qty"])
        price = float(item["price"])
        subtotal = qty * price
        total += subtotal

        pdf.cell(60, 8, rtl(str(subtotal)), border=1, align="C")
        pdf.cell(40, 8, rtl(str(price)), border=1, align="C")
        pdf.cell(30, 8, rtl(str(qty)), border=1, align="C")
        pdf.cell(60, 8, rtl(item["name"]), border=1, ln=True, align="C")

    pdf.ln(5)
    pdf.set_font("Amiri", "", 16)
    pdf.cell(0, 10, rtl(f"الإجمالي الكلي: {total} جنيه"), ln=True, align="R")

    # -------------------------------
    #   QR Code
    # -------------------------------
    import qrcode
    qr_text = f"Invoice: {order_id}\nTotal: {total}\nCustomer: {order['name']}"
    qr = qrcode.make(qr_text)
    qr_path = f"qr_{order_id}.png"
    qr.save(qr_path)

    pdf.image(qr_path, x=10, y=pdf.get_y() + 10, w=35)
    os.remove(qr_path)

    # -------------------------------
    #   الفوتر
    # -------------------------------
    pdf.ln(40)
    pdf.set_font("Amiri", "", 12)
    pdf.cell(0, 10, rtl(settings.get("footer", "")), ln=True, align="C")

    # إخراج PDF
    pdf_bytes = bytes(pdf.output(dest="S"))


    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f"inline; filename=invoice_{order_id}.pdf"
    return response


@app.route('/track/<order_id>')
def track_order(order_id):
    orders = load_data(ORDERS_FILE) or []
    pharmacy = load_data("pharmacy.json") or {}
    order = next((o for o in orders if o.get("order_id") == order_id), None)
    if not order:
        return "الطلب غير موجود", 404
    return render_template("track_order.html", order=order, pharmacy=pharmacy)


@app.route("/")
def index():
    products = load_data(PRODUCTS_FILE) or {}
    pharmacy = load_data("pharmacy.json") or {}
    return render_template("index.html", products=products, pharmacy=pharmacy)

@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    products = load_data(PRODUCTS_FILE) or {}
    pharmacy = load_data("pharmacy.json") or {}

    # ====== GET ======
    if request.method == "GET":
        return render_template("checkout.html", order_id=None, products=products, pharmacy=pharmacy)

    # ====== POST ======
    # Check IP rate limiting
    client_ip = get_client_ip()
    can_order, remaining_minutes = can_place_order(client_ip)
    
    if not can_order:
        return render_template(
            "checkout.html",
            order_id=None,
            products=products,
            pharmacy=pharmacy,
            message=f"⚠️ يمكنك تقديم طلب واحد فقط كل ساعة. يرجى المحاولة مرة أخرى بعد {remaining_minutes} دقيقة."
        )
    
    cart_json = request.form.get("cart")
    cart = json.loads(cart_json)

    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()

    if not name or not phone:
        return render_template(
            "checkout.html",
            order_id=None,
            products=products,
            pharmacy=pharmacy,
            message="❌ الرجاء إدخال جميع البيانات."
        )

    # ============================
    #   1) التحقق من توفر المخزون
    # ============================
    for pid, item in cart.items():
        requested = int(item["qty"])
        product = products.get(pid)
        if not product:
            return render_template("checkout.html", order_id=None, products=products, message=f"⚠️ المنتج {pid} غير موجود.")
        total_stock = sum(int(b.get("quantity",0)) for b in product.get("batches", []))

        if requested > total_stock:
            return render_template(
                "checkout.html",
                order_id=None,
                products=products,
                pharmacy=pharmacy,
                message=f"⚠️ الكمية المطلوبة من {product.get('name')} غير متوفرة (المتاح: {total_stock})."
            )

    # ============================================
    #   2) خصم المخزون من أقرب Batch (FEFO)
    # ============================================
    for pid, item in cart.items():
        needed = int(item["qty"])
        product = products[pid]

        # رتّب ال batches حسب تاريخ الانتهاء (الأقرب أولاً)
        batches = sorted(
            product.get("batches", []),
            key=lambda x: x.get("expiry_date","")
        )

        total_needed = needed
        cost_sum = 0.0

        for batch in batches:
            ensure_purchase_price(batch)
            if needed <= 0:
                break

            available = int(batch.get("quantity", 0))
            purchase_price = float(batch.get("purchase_price", batch.get("price", 0)))

            if available >= needed:
                cost_sum += needed * purchase_price
                batch["quantity"] = available - needed
                needed = 0
            else:
                cost_sum += available * purchase_price
                needed -= available
                batch["quantity"] = 0

        # حدّث الـ batches بعد الخصم
        products[pid]["batches"] = batches

        # سجّل متوسط تكلفة الشراء لهذا المنتج ضمن بيانات الطلب (يُستخدم لحساب الأرباح)
        avg_cost = (cost_sum / total_needed) if total_needed else 0
        cart[pid]["cost"] = avg_cost

    save_data(PRODUCTS_FILE, products)

    # ============================
    #   3) حفظ الطلب
    # ============================
    try:
        orders = load_data(ORDERS_FILE) or []
    except:
        orders = []

    # Generate random order ID and ensure it's unique
    order_id = generate_order_id()
    existing_ids = {o.get("order_id") for o in orders}
    while order_id in existing_ids:
        order_id = generate_order_id()

    total_price = sum(
        int(item["qty"]) * float(item["price"])
        for item in cart.values()
    )

    order = {
        "order_id": order_id,
        "name": name,
        "phone": phone,
        "items": cart,
        "total_price": total_price,
        "status": "قيد الانتظار",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    orders.append(order)
    save_data(ORDERS_FILE, orders)
    
    # Record IP address for rate limiting
    record_order_ip(client_ip)

    return render_template(
        "checkout.html",
        order_id=order_id,
        products=products,
        pharmacy=pharmacy
    )

@app.route('/admin/orders')
def admin_orders():
    if 'admin' not in session:
        return redirect(url_for('admin_login'))
    orders = load_data(ORDERS_FILE) or []
    return render_template('admin_orders.html', orders=orders)

@app.route('/admin/update_order/<order_id>', methods=['POST'])
def update_order(order_id):
    orders = load_data(ORDERS_FILE) or []
    data = request.get_json()
    new_status = data.get("status")

    # load products
    products = load_data(PRODUCTS_FILE) or {}

    for order in orders:
        if order["order_id"] == order_id:
            old_status = order.get("status")
            order["status"] = new_status

            # if changing from non-canceled to canceled -> restore stock
            if old_status != "ملغي" and new_status == "ملغي":
                for pid, item in order.get("items", {}).items():
                    pid = str(pid)
                    qty = int(item.get("qty", item.get("quantity", 0)))
                    if pid in products:
                        prod = products[pid]
                        # restore into an empty-expiry batch if exists, else append new batch
                        restored = False
                        for b in prod.get("batches", []):
                            if b.get("expiry_date", "") == "":
                                b["quantity"] = int(b.get("quantity",0)) + qty
                                restored = True
                                break
                        if not restored:
                            prod.setdefault("batches", []).append({
                                "price": float(item.get("price", 0)),
                                "quantity": qty,
                                "expiry_date": ""
                            })
                save_data(PRODUCTS_FILE, products)

            break

    save_data(ORDERS_FILE, orders)

    return "Saved", 200

@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == 'admin' and password == '123':
            session['admin'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            return render_template('admin_login.html', error="Invalid credentials")
    if 'admin' in session:
        return redirect(url_for('admin_dashboard'))
    return render_template('admin_login.html')

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'admin' not in session:
        return redirect(url_for('admin_login'))
    products = load_data(PRODUCTS_FILE) or {}
    return render_template('admin_dashboard.html', products=products)

@app.route('/admin/manual_order', methods=['GET', 'POST'])
def manual_order():
    if 'admin' not in session:
        return redirect(url_for('admin_login'))
    
    products = load_data(PRODUCTS_FILE) or {}
    
    if request.method == 'GET':
        return render_template('admin_manual_order.html', products=products)
    
    # POST: Create manual order
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    
    if not name:
        return render_template('admin_manual_order.html', products=products, error="الرجاء إدخال اسم العميل")
    
    # Parse items from form
    items_data = {}
    for key, value in request.form.items():
        if key.startswith("product_") and value:
            pid = key.replace("product_", "")
            try:
                qty = int(value)
                if qty > 0 and pid in products:
                    product = products[pid]
                    # Get selling price from first batch
                    sell_price = float(product.get("batches", [{}])[0].get("price", 0)) if product.get("batches") else 0
                    items_data[pid] = {
                        "name": product.get("name"),
                        "qty": qty,
                        "price": sell_price
                    }
            except (ValueError, TypeError):
                continue  # Skip invalid quantity values
    
    if not items_data:
        return render_template('admin_manual_order.html', products=products, error="الرجاء إضافة منتجات على الأقل")
    
    # Check stock availability
    for pid, item in items_data.items():
        product = products.get(pid)
        if not product:
            return render_template('admin_manual_order.html', products=products, error=f"المنتج {pid} غير موجود")
        total_stock = sum(int(b.get("quantity", 0)) for b in product.get("batches", []))
        if item["qty"] > total_stock:
            return render_template('admin_manual_order.html', products=products, error=f"الكمية المطلوبة من {item['name']} غير متوفرة (المتاح: {total_stock})")
    
    # Deduct stock and calculate costs (same logic as checkout)
    for pid, item in items_data.items():
        needed = item["qty"]
        product = products[pid]
        
        batches = sorted(
            product.get("batches", []),
            key=lambda x: x.get("expiry_date", "")
        )
        
        total_needed = needed
        cost_sum = 0.0
        
        for batch in batches:
            ensure_purchase_price(batch)
            if needed <= 0:
                break
            
            available = int(batch.get("quantity", 0))
            purchase_price = float(batch.get("purchase_price", batch.get("price", 0)))
            
            if available >= needed:
                cost_sum += needed * purchase_price
                batch["quantity"] = available - needed
                needed = 0
            else:
                cost_sum += available * purchase_price
                needed -= available
                batch["quantity"] = 0
        
        products[pid]["batches"] = batches
        avg_cost = (cost_sum / total_needed) if total_needed else 0
        items_data[pid]["cost"] = avg_cost
    
    save_data(PRODUCTS_FILE, products)
    
    # Create order
    orders = load_data(ORDERS_FILE) or []
    order_id = generate_order_id()
    existing_ids = {o.get("order_id") for o in orders}
    while order_id in existing_ids:
        order_id = generate_order_id()
    
    total_price = sum(int(item["qty"]) * float(item["price"]) for item in items_data.values())
    
    order = {
        "order_id": order_id,
        "name": name,
        "phone": phone or "غير محدد",
        "items": items_data,
        "total_price": total_price,
        "status": "مكتمل",  # Already completed since sold in-store
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    orders.append(order)
    save_data(ORDERS_FILE, orders)
    
    # Use session flash for success message (if flash was imported, otherwise redirect)
    return redirect(url_for('admin_orders'))

@app.route('/admin/add_product', methods=['POST'])
def add_product():
    products = load_data(PRODUCTS_FILE) or {}

    name = request.form.get("name")
    purchase_price = float(request.form.get("purchase_price", "0") or 0)
    sell_price = float(request.form.get("sell_price", "0") or 0)
    stock = request.form.get("stock", "0")
    expiry_date = request.form.get("expiry_date", "").strip()

    image = None
    image_url = request.form.get("image") or ""
    image_file = request.files.get("image_file")
    if image_file and image_file.filename:
        upload_dir = os.path.join("static", "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        filename = f"{len(products)+1}_{image_file.filename}"
        path = os.path.join(upload_dir, filename)
        image_file.save(path)
        image = f"uploads/{filename}"
    elif image_url:
        image = image_url

    if products:
        new_id = str(max(int(pid) for pid in products.keys()) + 1)
    else:
        new_id = "1"

    # create initial batch using provided stock and price
    batches = [{
        "price": float(sell_price),
        "purchase_price": float(purchase_price),
        "quantity": int(stock),
        "expiry_date": expiry_date
    }]

    products[new_id] = {
        "name": name,
        "image": image,
        "batches": batches
    }

    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=4)

    return redirect("/admin")

@app.route("/admin/edit_product/<pid>", methods=["POST"])
def admin_edit_product(pid):
    products = load_data(PRODUCTS_FILE) or {}

    if pid not in products:
        return "Product not found", 404

    product = products[pid]

    # ---------- JSON request ----------
    if request.content_type == "application/json":
        data = request.get_json()
        action = data.get("action")

        # edit main fields
        if action == "edit_main":
            field = data.get("field")
            value = data.get("value")
            product[field] = value

        elif action == "edit_batch":
            index = data.get("index")
            if "batches" not in product:
                product["batches"] = []

            if 0 <= index < len(product["batches"]):
                product["batches"][index]["price"] = float(data.get("price", 0))
                product["batches"][index]["purchase_price"] = float(data.get("purchase_price", data.get("price", 0)))
                product["batches"][index]["quantity"] = int(data.get("quantity", 0))
                product["batches"][index]["expiry_date"] = data.get("expiry_date")

        elif action == "add_batch":
            if "batches" not in product:
                product["batches"] = []

            product["batches"].append({
                "price": 0,
                "purchase_price": 0,
                "quantity": 0,
                "expiry_date": ""
            })

        elif action == "delete_batch":
            index = data.get("index")
            if 0 <= index < len(product.get("batches", [])):
                del product["batches"][index]

        elif action == "delete_product":
            del products[pid]

        save_data(PRODUCTS_FILE, products)
        return "OK"

    # ---------- image upload ----------
    if "image" in request.files:
        image = request.files["image"]

        if image.filename != "":
            upload_dir = os.path.join("static", "uploads")
            os.makedirs(upload_dir, exist_ok=True)

            filename = f"{pid}_{image.filename}"
            path = os.path.join(upload_dir, filename)

            image.save(path)

            product["image"] = f"uploads/{filename}"

            save_data(PRODUCTS_FILE, products)

        return "IMAGE_UPLOADED"

    return "Invalid Request"

@app.route('/admin/delete_product/<pid>')
def delete_product(pid):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    products = load_data(PRODUCTS_FILE) or {}
    if pid in products:
        del products[pid]
        save_data(PRODUCTS_FILE, products)
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/products")
def admin_products():
    if "admin" not in session:
        return redirect(url_for("admin_login"))
    # Redirect to dashboard where products are managed
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_login"))

@app.route("/add_to_cart/<product_id>", methods=["GET"])
def add_to_cart(product_id):
    products = load_data(PRODUCTS_FILE) or {}

    if product_id not in products:
        return {"status": "error", "message": "المنتج غير موجود"}, 404

    product = products[product_id]

    total_stock = 0
    batches = product.get("batches", [])

    for b in batches:
        total_stock += int(b.get("quantity", 0))

    requested_qty = request.args.get("qty", default=1, type=int)

    print("Total Stock =", total_stock, "| Requested =", requested_qty)

    if requested_qty > total_stock:
        return {
            "status": "error",
            "message": f"⚠️ الكمية المطلوبة غير متوفرة، المتاح فقط: {total_stock}"
        }

    return {"status": "success"}

@app.route('/admin/reports')
def admin_reports():
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    orders = load_data(ORDERS_FILE) or []
    total_orders = len(orders)
    total_revenue = sum(float(o.get("total_price", 0)) for o in orders if o.get("status") != "ملغي")
    counter = Counter()
    for o in orders:
        if o.get("status") == "ملغي":
            continue
        for pid, item in o.get("items", {}).items():
            counter[item.get("name","unknown")] += int(item.get("qty", 0))
    top_products = counter.most_common(10)
    # compute expiring count
    products = load_data(PRODUCTS_FILE) or {}
    expiring_count = 0
    now = datetime.now().date()
    for pid,p in products.items():
        for b in p.get("batches", []):
            ed = b.get("expiry_date","")
            if ed:
                try:
                    edd = datetime.strptime(ed, "%Y-%m-%d").date()
                    if (edd - now).days <= 30:
                        expiring_count += 1
                except:
                    continue

    return jsonify({
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "top_products": top_products,
        "expiring_count": expiring_count
    })

@app.route('/admin/profits')
def admin_profits():
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    orders = load_data(ORDERS_FILE) or []
    completed_orders = [o for o in orders if o.get("status") == "مكتمل"]

    def add_bucket(store, key, revenue, cost):
        if key not in store:
            store[key] = {"revenue": 0, "cost": 0}
        store[key]["revenue"] += revenue
        store[key]["cost"] += cost

    daily = {}
    weekly = {}
    monthly = {}

    total_revenue = 0
    total_cost = 0

    for o in completed_orders:
        try:
            dt = datetime.strptime(o.get("created_at",""), "%Y-%m-%d %H:%M:%S")
        except:
            continue

        revenue = 0
        cost = 0
        for pid, item in o.get("items", {}).items():
            qty = int(item.get("qty", 0))
            price = float(item.get("price", 0))
            unit_cost = float(item.get("cost", item.get("price", 0)))
            revenue += qty * price
            cost += qty * unit_cost

        total_revenue += revenue
        total_cost += cost

        day_key = dt.strftime("%Y-%m-%d")
        month_key = dt.strftime("%Y-%m")
        iso_year, iso_week, _ = dt.isocalendar()
        week_key = f"الأسبوع {iso_week} من {iso_year}"

        add_bucket(daily, day_key, revenue, cost)
        add_bucket(weekly, week_key, revenue, cost)
        add_bucket(monthly, month_key, revenue, cost)

    def to_list(store):
        out = []
        for k,v in sorted(store.items()):
            profit = v["revenue"] - v["cost"]
            out.append({"period": k, "revenue": v["revenue"], "cost": v["cost"], "profit": profit})
        return out

    return render_template(
        "admin_profits.html",
        daily=to_list(daily),
        weekly=to_list(weekly),
        monthly=to_list(monthly),
        totals={
            "revenue": total_revenue,
            "cost": total_cost,
            "profit": total_revenue - total_cost
        },
        completed_count=len(completed_orders)
    )

@app.route('/admin/expiring')
def admin_expiring():
    if 'admin' not in session:
        return redirect(url_for('admin_login'))
    days = int(request.args.get("days", 30))
    products = load_data(PRODUCTS_FILE) or {}
    soon = []
    now = datetime.now().date()
    for pid, p in products.items():
        for b in p.get("batches", []):
            if not b.get("expiry_date"): continue
            try:
                ed = datetime.strptime(b["expiry_date"], "%Y-%m-%d").date()
                delta = (ed - now).days
                if delta <= days:
                    soon.append({
                        "pid": pid,
                        "name": p.get("name"),
                        "expiry_date": b["expiry_date"],
                        "quantity": b.get("quantity",0),
                        "days_left": delta
                    })
            except:
                continue
    return jsonify(soon)


@app.route('/admin/stock_overview')
def stock_overview():
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    products = load_data(PRODUCTS_FILE) or {}

    stock_list = []
    now = datetime.now().date()

    for pid, p in products.items():
        batches = p.get("batches", [])

        total_qty = sum(int(b.get("quantity", 0)) for b in batches)

        # أقرب تاريخ انتهاء
        expiry_dates = [
            datetime.strptime(b["expiry_date"], "%Y-%m-%d").date()
            for b in batches
            if b.get("expiry_date")
        ]

        if expiry_dates:
            nearest_exp = min(expiry_dates)
            days_left = (nearest_exp - now).days
        else:
            nearest_exp = None
            days_left = None

        if total_qty == 0:
            status = "out"
        elif days_left is not None and days_left <= 0:
            status = "out"
        elif days_left is not None and days_left <= 30:
            status = "expire_soon"
        elif total_qty <= 5:
            status = "low"
        else:
            status = "ok"

        stock_list.append({
            "pid": pid,
            "name": p.get("name"),
            "total_qty": total_qty,
            "nearest_exp": nearest_exp.strftime("%Y-%m-%d") if nearest_exp else "-",
            "days_left": days_left if days_left is not None else "-",
            "status": status
        })

    return render_template("admin_stock_overview.html", items=stock_list)




@app.route("/admin/expiring_count")
def expiring_count():
    if "admin" not in session:
        return jsonify({"count": 0})

    products = load_data(PRODUCTS_FILE) or {}
    now = datetime.now().date()
    count = 0

    for pid, p in products.items():
        for b in p.get("batches", []):
            exp = b.get("expiry_date", "")
            if exp:
                try:
                    d = datetime.strptime(exp, "%Y-%m-%d").date()
                    if (d - now).days <= 30:
                        count += 1
                except:
                    pass

    return jsonify({"count": count})





if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
