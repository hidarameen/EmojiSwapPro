
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
