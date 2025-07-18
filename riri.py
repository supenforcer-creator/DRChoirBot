import os
import asyncio
import logging
from telegram import Update, Message
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import AsyncGroq
import json
from datetime import datetime, timedelta
from typing import Dict, List
import sqlite3
from contextlib import asynccontextmanager

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class DRChoirBot:
    def __init__(self):
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.groq_api_key = os.getenv('GROQ_API_KEY')
        
        if not self.telegram_token or not self.groq_api_key:
            raise ValueError("Please set TELEGRAM_BOT_TOKEN and GROQ_API_KEY environment variables")
        
        self.groq_client = AsyncGroq(api_key=self.groq_api_key)
        self.init_database()
        
        # Rate limiting: track requests per minute
        self.request_tracker = {}
        
        # System prompt for scripture and music discussions
        self.system_prompt = """You are a knowledgeable, wise and friendly assistant helping with discussions and answering questions about scriptures and music. 
        You should:
        - Provide thoughtful insights on questions asked--biblical or spiritual questions should be given due diligence and proper consideration of context is paramount
        - Help with music theory, worship songs, chord progressions, song recommendations based on vocal type, range, goal or objective, and Christian music discussions
        - Act like one of us. Your name is Riri and you are part of the Dead Raisers Choir in Grace Edge Ministries (aka Dead Raisers). We love Jesus and we are a people of fire
        - Keep responses detailed and but meaningful (2-3 sentences max for group chat, except for follow-ups/responses for clarification purposes, which can be longer)
        - Depending on the topic, suggest to them that they can find out more in details but don't present it as a question
        - Reference specific verses when relevant or appropriate
        - Encourage growth, excellence, healthy discussion and reflection
        
        You belong. Act like us. Make jokes even, if appropriate. Keep an enthusiastic, tone, full of awe, wonder and desire."""

    def init_database(self):
        """Initialize SQLite database for conversation history"""
        import os
        # Use /tmp for Render.com (ephemeral storage)
        db_path = '/tmp/conversations.db' if os.getenv('RENDER') else 'conversations.db'
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                username TEXT,
                message TEXT,
                response TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()

    async def check_rate_limit(self, chat_id: int) -> bool:
        """Check if chat is within rate limits (max 100 requests per hour)"""
        now = datetime.now()
        hour_ago = now - timedelta(hours=1)
        
        if chat_id not in self.request_tracker:
            self.request_tracker[chat_id] = []
        
        # Clean old requests
        self.request_tracker[chat_id] = [
            req_time for req_time in self.request_tracker[chat_id] 
            if req_time > hour_ago
        ]
        
        # Check if under limit
        if len(self.request_tracker[chat_id]) >= 100:
            return False
        
        # Add current request
        self.request_tracker[chat_id].append(now)
        return True

    async def get_conversation_context(self, chat_id: int, limit: int = 10) -> List[Dict]:
        """Get recent conversation context for better responses"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT message, response FROM conversations 
            WHERE chat_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (chat_id, limit))
        
        messages = cursor.fetchall()
        context = []
        
        for message, response in reversed(messages):
            context.extend([
                {"role": "user", "content": message},
                {"role": "assistant", "content": response}
            ])
        
        return context

    async def save_conversation(self, chat_id: int, user_id: int, username: str, message: str, response: str):
        """Save conversation to database"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO conversations (chat_id, user_id, username, message, response)
            VALUES (?, ?, ?, ?, ?)
        ''', (chat_id, user_id, username, message, response))
        self.conn.commit()

    async def generate_response(self, message: str, chat_id: int) -> str:
        """Generate response using Groq API"""
        try:
            # Get conversation context
            context = await self.get_conversation_context(chat_id)
            
            # Build messages for API
            messages = [{"role": "system", "content": self.system_prompt}]
            messages.extend(context)
            messages.append({"role": "user", "content": message})
            
            # Keep only last 15 messages to stay within token limits
            if len(messages) > 16:
                messages = messages[:1] + messages[-15:]
            
            # Call Groq API
            response = await self.groq_client.chat.completions.create(
                model="llama3-70b-8192",  # Fast and capable model
                messages=messages,
                max_tokens=300,  # Keep responses concise for group chat
                temperature=0.7,
                stream=False
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"Error generating response: {e}")
            return "Sorry, I'm having trouble responding right now. Please try again in a moment."

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_message = """Hey there! I'm Riri, here to help you as you aim to get better as a minister in DR's Choir.

Just ask me anything about:
🎵 Worship, songs to score for your kind of voice/range, music theory, etc
📖 Bible verses, stories, or concepts

Just mention me in group chats or message me directly to start our conversation!

Use /help for more commands."""
        
        await update.message.reply_text(welcome_message)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_text = """🤖 **Scripture & Music Bot Commands:**

/start - Welcome message
/help - Show this help
/stats - Show bot statistics
/clear - Clear conversation history for this chat

**How to use:**
- In group chats: Just mention me (@Riri) or reply to my messages
- In private chats: Send any message
- Ask about Bible verses, music theory, worship songs, or spiritual topics

**Examples:**
- "What does Hosanna mean?"
- "Help me understand this chord progression"
- "What are your thoughts on leading a worship session?"
"""
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show bot statistics"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM conversations WHERE chat_id = ?', (update.effective_chat.id,))
        chat_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM conversations')
        total_count = cursor.fetchone()[0]
        
        stats_text = f"""📊 **Bot Statistics:**

This chat: {chat_count} conversations
Total across all chats: {total_count} conversations

Bot uptime: Online and ready! 🟢"""
        
        await update.message.reply_text(stats_text, parse_mode='Markdown')

    async def clear_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Clear conversation history for current chat"""
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM conversations WHERE chat_id = ?', (update.effective_chat.id,))
        self.conn.commit()
        
        await update.message.reply_text("✅ Conversation history cleared for this chat!")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle regular messages"""
        message = update.message
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        username = update.effective_user.username or update.effective_user.first_name
        
        # Check if bot should respond
        should_respond = False
        
        if chat_id > 0:  # Private chat
            should_respond = True
        else:  # Group chat
            # Respond if mentioned or replying to bot
            if message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id:
                should_respond = True
            elif f"@{context.bot.username}" in message.text:
                should_respond = True
            elif any(keyword in message.text.lower() for keyword in ['bible', 'scripture', 'verse', 'book', 'god', 'jesus', 'music', 'song', 'chord', 'Dead Raisers', 'love']):
                should_respond = True
        
        if not should_respond:
            return
        
        # Rate limiting
        if not await self.check_rate_limit(chat_id):
            await message.reply_text("⏰ Chat rate limit reached. Please wait a moment before asking again.")
            return
        
        # Show typing indicator
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        # Generate response
        user_message = message.text
        response = await self.generate_response(user_message, chat_id)
        
        # Save conversation
        await self.save_conversation(chat_id, user_id, username, user_message, response)
        
        # Send response
        await message.reply_text(response)

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Exception while handling an update: {context.error}")

    def run(self):
        """Start the bot"""
        application = Application.builder().token(self.telegram_token).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("stats", self.stats_command))
        application.add_handler(CommandHandler("clear", self.clear_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        # Add error handler
        application.add_error_handler(self.error_handler)
        
        # Start the bot
        logger.info("I'm coming alive...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    bot = DRChoirBot()
    bot.run()
