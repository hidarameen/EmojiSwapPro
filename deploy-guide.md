# دليل نشر البوت على Northflank

## المتطلبات المسبقة

### 1. إنشاء حساب Northflank
- قم بالتسجيل في [Northflank](https://northflank.com)
- أنشئ مشروع جديد

### 2. إعداد قاعدة البيانات
- أضف PostgreSQL addon في Northflank
- أو استخدم خدمة خارجية مثل Supabase/Railway

### 3. الحصول على بيانات Telegram API
- انتقل إلى [my.telegram.org](https://my.telegram.org)
- احصل على `API_ID` و `API_HASH`
- قم بتشغيل `python generate_session.py` للحصول على `SESSION_STRING`

## خطوات النشر

### 1. إعداد المشروع
```bash
git clone your-repo
cd telegram-emoji-bot
```

### 2. إنشاء Combined Service في Northflank
1. انتقل إلى مشروعك → Create new → Service
2. اختر **Combined Service**
3. اختر **Git Repository** أو **Docker Hub**

### 3. إعداد Build Configuration
- **Build Type**: Dockerfile
- **Dockerfile Path**: `./Dockerfile`
- **Build Context**: `/`

### 4. إعداد Environment Variables
انتقل إلى **Environment Variables** وأضف:

#### متغيرات مطلوبة:
```
API_ID=your_telegram_api_id
API_HASH=your_telegram_api_hash
SESSION_STRING=your_telethon_session_string
DATABASE_URL=postgresql://user:pass@host:port/dbname
```

#### متغيرات اختيارية:
```
LOG_LEVEL=INFO
ENVIRONMENT=production
SENTRY_DSN=your_sentry_dsn_for_monitoring
```

### 5. إعداد Resources
- **Memory**: 512MB (minimum 256MB)
- **CPU**: 0.2 vCPU
- **Replicas**: 1
- **Storage**: 1GB (للوقز)

### 6. إعداد Health Check
- **Type**: CMD
- **Command**: `python -c "import sys; sys.exit(0)"`
- **Interval**: 60s
- **Timeout**: 30s

### 7. Deploy
اضغط **Deploy** وانتظر إكمال البناء والنشر

## إدارة الـ Deployment

### رؤية الـ Logs
```bash
# في Northflank Dashboard
Services → Your Bot → Logs
```

### إعادة النشر
```bash
# Push to main branch سيؤدي إلى إعادة نشر تلقائي
git push origin main
```

### Scaling
```bash
# في Northflank Dashboard
Services → Your Bot → Resources → Scale
```

## استكشاف الأخطاء

### مشاكل شائعة:

1. **Session String خطأ**
   - أعد تشغيل `generate_session.py`
   - تأكد من API_ID و API_HASH صحيحة

2. **Database Connection**
   - تحقق من DATABASE_URL
   - تأكد من أن PostgreSQL addon يعمل

3. **Memory Issues**
   - زد Memory إلى 512MB أو أكثر
   - راقب استخدام الذاكرة في Metrics

4. **Bot لا يستجيب**
   - تحقق من الـ logs للأخطاء
   - تأكد من أن البوت مفعل مع @BotFather

## الأمان

- استخدم Secrets Groups لحفظ المتغيرات الحساسة
- فعّل HTTPS للـ health checks
- استخدم non-root user في Container (مُفعّل افتراضياً)

## المراقبة

### Metrics متاحة:
- CPU Usage
- Memory Usage  
- Network I/O
- Container Restarts

### Alerts:
- إعداد تنبيهات للـ deployment failures
- إعداد تنبيهات لاستخدام الذاكرة العالي
- إعداد تنبيهات للأخطاء في الـ logs

## CI/CD التلقائي

لإعداد نشر تلقائي عند push:

1. أنشئ `.github/workflows/deploy.yml`
2. أضف NORTHFLANK_TOKEN في GitHub Secrets
3. كل push للـ main branch سيؤدي لإعادة نشر