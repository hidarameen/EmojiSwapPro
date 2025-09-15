
#!/usr/bin/env python3
"""
سكريبت تشغيل بوت التحكم
"""

import asyncio
import sys
from control_bot import main

if __name__ == "__main__":
    try:
        print("🚀 بدء تشغيل بوت التحكم...")
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️ تم إيقاف بوت التحكم بواسطة المستخدم")
        sys.exit(0)
    except Exception as e:
        print(f"❌ خطأ في بوت التحكم: {e}")
        sys.exit(1)
#!/usr/bin/env python3
"""
تشغيل البوت الرسمي للتحكم
يجب الحصول على CONTROL_BOT_TOKEN من BotFather أولاً
"""

import asyncio
import os
from dotenv import load_dotenv
from control_bot import main

if __name__ == "__main__":
    # Load environment variables
    load_dotenv()
    
    # Check required environment variables
    required_vars = ['CONTROL_BOT_TOKEN', 'DATABASE_URL']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print("❌ متغيرات البيئة المطلوبة غير موجودة:")
        for var in missing_vars:
            print(f"   • {var}")
        print("\n📝 تأكد من إضافة هذه المتغيرات في ملف .env")
        exit(1)
    
    print("🚀 بدء تشغيل البوت الرسمي للتحكم...")
    print("💡 يجب أن يكون UserBot يعمل في نفس الوقت للحصول على أفضل أداء")
    
    asyncio.run(main())
