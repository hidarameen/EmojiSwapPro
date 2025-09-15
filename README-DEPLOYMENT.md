# 🚀 دليل نشر البوت على Northflank

## 📋 الملفات المطلوبة (تم إنشاؤها)

✅ **Dockerfile** - ملف Docker محسّن للبوت
✅ **requirements-docker.txt** - قائمة التبعيات للإنتاج
✅ **.dockerignore** - تحسين عملية البناء
✅ **docker-compose.yml** - للتطوير المحلي
✅ **northflank-deploy.json** - إعدادات Northflank
✅ **.env.example** - مثال على المتغيرات المطلوبة

## 🔧 خطوات التحضير السريع

### 1. إعداد Session String
```bash
python generate_session.py
```
احفظ الـ SESSION_STRING للاستخدام في Northflank

### 2. بناء واختبار محلي (اختياري)
```bash
# بناء الصورة
docker build -t telegram-bot .

# تشغيل مع متغيرات البيئة
docker run --env-file .env telegram-bot
```

### 3. نشر على Northflank

#### A. عبر Git Repository (الطريقة المُفضلة)
1. ارفع الكود إلى GitHub/GitLab
2. في Northflank: **Create Service** → **Git Repository**
3. اختر repo وبرانش `main`
4. سيتم اكتشاف Dockerfile تلقائياً

#### B. عبر Docker Build
1. في Northflank: **Create Service** → **Docker Build**
2. اختر **Dockerfile** build type
3. حدد مسار الـ Dockerfile: `./Dockerfile`

## ⚙️ إعدادات Northflank الأساسية

### Environment Variables (مطلوبة):
```bash
API_ID=your_telegram_api_id
API_HASH=your_telegram_api_hash  
SESSION_STRING=your_telethon_session_string
DATABASE_URL=postgresql://user:pass@host:port/dbname
```

### Resources (منصوح بها):
- **Memory**: 512MB
- **CPU**: 0.2 vCPU  
- **Replicas**: 1
- **Storage**: 1GB

### Health Check:
- **Type**: Command
- **Command**: `python -c "import sys; sys.exit(0)"`
- **Interval**: 60s

## 🔐 الأمان والمتغيرات

### إنشاء Secrets Group:
1. **Project Settings** → **Secrets**
2. أنشئ group جديد: `telegram-bot-secrets`
3. أضف المتغيرات الحساسة:
   - API_ID
   - API_HASH
   - SESSION_STRING
   - DATABASE_URL

### ربط Secrets بالخدمة:
1. **Service Settings** → **Environment**
2. **Link Secrets Group** → اختر `telegram-bot-secrets`

## 📊 المراقبة والصيانة

### رؤية الـ Logs:
- **Service Dashboard** → **Logs**
- أو استخدم CLI: `northflank logs service --service-id=your-service-id`

### Metrics:
- CPU/Memory usage
- Network I/O  
- Container restarts
- Response times

### Alerts (إعداد تنبيهات):
- فشل الـ deployment
- استخدام ذاكرة عالي (>80%)
- إعادة تشغيل متكررة

## 🗄️ إعداد قاعدة البيانات

### PostgreSQL على Northflank:
1. **Add-ons** → **PostgreSQL**
2. اختر Plan (Pro أو Business)
3. الـ DATABASE_URL سيكون متاح تلقائياً

### أو استخدم خدمة خارجية:
- **Supabase** (مجاني + مدفوع)
- **Railway** (مجاني محدود)
- **PlanetScale** (MySQL - يحتاج تعديل في الكود)

## 🔄 CI/CD التلقائي

### إعداد Auto-Deploy:
1. **Service Settings** → **CI/CD**
2. فعّل **Auto-deploy on push to main**
3. أو أنشئ GitHub Action:

```yaml
# .github/workflows/deploy.yml
name: Deploy to Northflank
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: northflank/deploy-to-northflank@v1
        with:
          token: ${{ secrets.NORTHFLANK_TOKEN }}
          service-id: your-service-id
```

## ❌ استكشاف الأخطاء الشائعة

### البوت لا يعمل:
1. تحقق من الـ logs للأخطاء
2. تأكد من صحة SESSION_STRING
3. تحقق من اتصال قاعدة البيانات

### مشاكل الذاكرة:
1. زد الـ memory إلى 512MB أو 1GB
2. راقب استخدام الذاكرة في Metrics

### مشاكل Database:
1. تأكد من صحة DATABASE_URL
2. تحقق من أن PostgreSQL addon يعمل
3. اختبر الاتصال محلياً

## 💰 التكلفة المتوقعة

### Northflank Pricing (تقريبي):
- **Startup Plan**: $20/month
  - 1GB RAM, 0.5 vCPU
  - PostgreSQL addon
  - مناسب للبوتات الصغيرة-المتوسطة

- **Pro Plan**: $60/month  
  - 4GB RAM, 2 vCPU
  - مناسب للبوتات الكبيرة مع قنوات كثيرة

### تحسين التكلفة:
- ابدأ بـ 256MB RAM و scale حسب الحاجة
- استخدم external database مجاني إذا أمكن
- راقب الـ metrics لتحسين الموارد

---

🎉 **البوت جاهز للنشر!** تأكد من اختبار جميع الوظائف بعد النشر.