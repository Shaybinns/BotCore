import psycopg2
import os
import json
from datetime import datetime, date
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv

load_dotenv()

# Database table name constant
LONG_TERM_DB = "long_term_memory"

def get_db_connection():
    """Get connection to database using Railway's injected DATABASE_URL"""
    return psycopg2.connect(os.getenv('DATABASE_URL'))

def create_user_profile(user_id: str, profile_data: Dict[str, Any]) -> bool:
    """
    Create or update user profile with investment preferences and goals
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Extract profile fields
        risk_tolerance = profile_data.get('risk_tolerance')
        investment_goal = profile_data.get('investment_goal')
        asset_preferences = profile_data.get('asset_preferences', {})
        industry_preferences = profile_data.get('industry_preferences', {})
        investment_style = profile_data.get('investment_style')
        
        # Check if user exists
        cursor.execute(f"""
            SELECT user_id FROM {LONG_TERM_DB}
            WHERE user_id = %s
        """, (user_id,))
        
        exists = cursor.fetchone()
        
        if exists:
            # Update existing user
            cursor.execute(f"""
                UPDATE {LONG_TERM_DB}
                SET risk_tolerance = %s,
                    investment_goal = %s,
                    asset_preferences = %s,
                    industry_preferences = %s,
                    investment_style = %s
                WHERE user_id = %s
            """, (json.dumps(risk_tolerance), json.dumps(investment_goal),
                  json.dumps(asset_preferences), json.dumps(industry_preferences),
                  json.dumps(investment_style), user_id))
        else:
            # Create new user
            cursor.execute(f"""
                INSERT INTO {LONG_TERM_DB}
                (user_id, created_at, risk_tolerance, investment_goal, 
                 asset_preferences, industry_preferences, investment_style)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (user_id, datetime.now().date(), json.dumps(risk_tolerance),
                  json.dumps(investment_goal), json.dumps(asset_preferences),
                  json.dumps(industry_preferences), json.dumps(investment_style)))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error creating/updating user profile: {e}")
        return False

def update_portfolio_holdings(user_id: str, holdings: Dict[str, Any]) -> bool:
    """Update user's portfolio holdings"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            INSERT INTO {LONG_TERM_DB} (user_id, portfolio_holdings, created_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) 
            DO UPDATE SET portfolio_holdings = %s
        """, (user_id, json.dumps(holdings), datetime.now().date(), json.dumps(holdings)))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error updating portfolio holdings: {e}")
        return False

def update_portfolio_performance(user_id: str, performance: Dict[str, Any]) -> bool:
    """Update user's portfolio performance metrics"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Add timestamp to performance data
        performance["last_updated"] = datetime.now().isoformat()
        
        cursor.execute(f"""
            INSERT INTO {LONG_TERM_DB} (user_id, portfolio_performance, created_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) 
            DO UPDATE SET portfolio_performance = %s
        """, (user_id, json.dumps(performance), datetime.now().date(), json.dumps(performance)))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error updating portfolio performance: {e}")
        return False

def add_user_transaction(user_id: str, transaction: Dict[str, Any]) -> bool:
    """Add a new transaction to user's history"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get existing transactions
        cursor.execute(f"""
            SELECT user_transactions FROM {LONG_TERM_DB}
            WHERE user_id = %s
        """, (user_id,))
        
        result = cursor.fetchone()
        if result and result[0]:
            transactions = result[0]
        else:
            transactions = []
        
        # Add new transaction with timestamp
        transaction["timestamp"] = datetime.now().isoformat()
        transactions.append(transaction)
        
        # Keep only last 100 transactions
        if len(transactions) > 100:
            transactions = transactions[-100:]
        
        cursor.execute(f"""
            INSERT INTO {LONG_TERM_DB} (user_id, user_transactions, created_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) 
            DO UPDATE SET user_transactions = %s
        """, (user_id, json.dumps(transactions), datetime.now().date(), json.dumps(transactions)))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error adding transaction: {e}")
        return False

def update_user_goals(user_id: str, goals: Dict[str, Any]) -> bool:
    """Update user's investment goals"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            INSERT INTO {LONG_TERM_DB} (user_id, user_goals, created_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) 
            DO UPDATE SET user_goals = %s
        """, (user_id, json.dumps(goals), datetime.now().date(), json.dumps(goals)))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error updating user goals: {e}")
        return False

def update_user_pathway(user_id: str, pathway: Dict[str, Any]) -> bool:
    """Update user's investment pathway/strategy"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            INSERT INTO {LONG_TERM_DB} (user_id, user_pathway, created_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) 
            DO UPDATE SET user_pathway = %s
        """, (user_id, json.dumps(pathway), datetime.now().date(), json.dumps(pathway)))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error updating user pathway: {e}")
        return False

def get_user_profile(user_id: str) -> Dict[str, Any]:
    """Get complete user profile data"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT risk_tolerance, investment_goal, asset_preferences, 
                   industry_preferences, investment_style, created_at
            FROM {LONG_TERM_DB}
            WHERE user_id = %s
        """, (user_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not result:
            return {}
        
        risk_tolerance, investment_goal, asset_preferences, industry_preferences, investment_style, created_at = result
        
        return {
            "risk_tolerance": risk_tolerance,
            "investment_goal": investment_goal,
            "asset_preferences": asset_preferences or {},
            "industry_preferences": industry_preferences or {},
            "investment_style": investment_style,
            "created_at": created_at
        }
        
    except Exception as e:
        print(f"Error retrieving user profile: {e}")
        return {}

def get_portfolio_data(user_id: str) -> Dict[str, Any]:
    """Get user's portfolio holdings and performance"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT portfolio_holdings, portfolio_performance
            FROM {LONG_TERM_DB}
            WHERE user_id = %s
        """, (user_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not result:
            return {"holdings": {}, "performance": {}}
        
        holdings, performance = result
        
        return {
            "holdings": holdings or {},
            "performance": performance or {}
        }
        
    except Exception as e:
        print(f"Error retrieving portfolio data: {e}")
        return {"holdings": {}, "performance": {}}

def get_user_transactions(user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Get user's recent transactions"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT user_transactions FROM {LONG_TERM_DB}
            WHERE user_id = %s
        """, (user_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not result or not result[0]:
            return []
        
        transactions = result[0]
        return transactions[-limit:] if limit else transactions
        
    except Exception as e:
        print(f"Error retrieving transactions: {e}")
        return []

def get_user_goals_and_pathway(user_id: str) -> Dict[str, Any]:
    """Get user's goals and investment pathway"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT user_goals, user_pathway
            FROM {LONG_TERM_DB}
            WHERE user_id = %s
        """, (user_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not result:
            return {"goals": {}, "pathway": {}}
        
        goals, pathway = result
        
        return {
            "goals": goals or {},
            "pathway": pathway or {}
        }
        
    except Exception as e:
        print(f"Error retrieving goals and pathway: {e}")
        return {"goals": {}, "pathway": {}}

def get_user_facts(user_id: str) -> str:
    """
    Get comprehensive user facts for brain.py integration
    Formats all long-term memory data into a readable string
    """
    try:
        profile = get_user_profile(user_id)
        portfolio = get_portfolio_data(user_id)
        transactions = get_user_transactions(user_id, 5)  # Last 5 transactions
        goals_pathway = get_user_goals_and_pathway(user_id)
        
        facts = []
        
        # Profile information
        if profile:
            facts.append("=== USER PROFILE ===")
            if profile.get("risk_tolerance"):
                facts.append(f"Risk Tolerance: {profile['risk_tolerance']}")
            if profile.get("investment_goal"):
                facts.append(f"Investment Goal: {profile['investment_goal']}")
            if profile.get("investment_style"):
                facts.append(f"Investment Style: {profile['investment_style']}")
            if profile.get("asset_preferences"):
                facts.append(f"Asset Preferences: {profile['asset_preferences']}")
            if profile.get("industry_preferences"):
                facts.append(f"Industry Preferences: {profile['industry_preferences']}")
            if profile.get("created_at"):
                facts.append(f"Profile Created: {profile['created_at']}")
        
        # Portfolio information
        if portfolio.get("holdings"):
            facts.append("\n=== PORTFOLIO HOLDINGS ===")
            facts.append(f"Current Holdings: {portfolio['holdings']}")
        
        if portfolio.get("performance"):
            facts.append("\n=== PORTFOLIO PERFORMANCE ===")
            facts.append(f"Performance Metrics: {portfolio['performance']}")
        
        # Recent transactions
        if transactions:
            facts.append("\n=== RECENT TRANSACTIONS ===")
            for i, transaction in enumerate(transactions, 1):
                facts.append(f"Transaction {i}: {transaction}")
        
        # Goals and pathway
        if goals_pathway.get("goals"):
            facts.append("\n=== INVESTMENT GOALS ===")
            facts.append(f"Goals: {goals_pathway['goals']}")
        
        if goals_pathway.get("pathway"):
            facts.append("\n=== INVESTMENT PATHWAY ===")
            facts.append(f"Strategy: {goals_pathway['pathway']}")
        
        return "\n".join(facts) if facts else "No long-term memory data available for this user."
        
    except Exception as e:
        print(f"Error generating user facts: {e}")
        return "Error retrieving user facts."

def save_result(user_id: str, result: str) -> bool:
    """
    Save command result to long-term memory
    This maintains compatibility with existing brain.py usage
    """
    try:
        # For now, we'll store results in a simple format
        # This could be enhanced to extract structured data from results
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get existing results or create new list
        cursor.execute(f"""
            SELECT user_transactions FROM {LONG_TERM_DB}
            WHERE user_id = %s
        """, (user_id,))
        
        result_data = cursor.fetchone()
        if result_data and result_data[0]:
            # Use existing transactions field to store results for now
            # In a full implementation, you might want a separate results field
            pass
        
        # For compatibility, we'll just return True
        # The actual result storage could be enhanced based on specific needs
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error saving result: {e}")
        return False

def get_latest_result(command_name: str, symbol: str = None) -> Optional[Dict[str, Any]]:
    """Get the latest result for a specific command"""
    # This could be enhanced to actually retrieve command results
    # For now, return None to maintain compatibility
    return None

def clear_user_data(user_id: str) -> bool:
    """Clear all long-term memory data for a user"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            DELETE FROM {LONG_TERM_DB}
            WHERE user_id = %s
        """, (user_id,))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error clearing user data: {e}")
        return False

def get_user_data_summary(user_id: str) -> Dict[str, Any]:
    """Get comprehensive summary of user's long-term memory data"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT risk_tolerance, investment_goal, asset_preferences, 
                   industry_preferences, investment_style, portfolio_holdings,
                   portfolio_performance, user_transactions, user_goals,
                   user_pathway, created_at
            FROM {LONG_TERM_DB}
            WHERE user_id = %s
        """, (user_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not result:
            return {
                "user_id": user_id,
                "status": "no_data",
                "created_at": None,
                "profile_completeness": 0,
                "has_portfolio": False,
                "transaction_count": 0,
                "has_goals": False
            }
        
        (risk_tolerance, investment_goal, asset_preferences, industry_preferences,
         investment_style, portfolio_holdings, portfolio_performance,
         user_transactions, user_goals, user_pathway, created_at) = result
        
        # Calculate profile completeness
        profile_fields = [risk_tolerance, investment_goal, investment_style]
        completed_fields = sum(1 for field in profile_fields if field)
        profile_completeness = (completed_fields / len(profile_fields)) * 100
        
        return {
            "user_id": user_id,
            "status": "active",
            "created_at": created_at,
            "profile_completeness": f"{profile_completeness:.1f}%",
            "has_portfolio": bool(portfolio_holdings),
            "transaction_count": len(user_transactions) if user_transactions else 0,
            "has_goals": bool(user_goals),
            "has_pathway": bool(user_pathway),
            "risk_tolerance": risk_tolerance,
            "investment_goal": investment_goal,
            "investment_style": investment_style
        }
        
    except Exception as e:
        print(f"Error getting user data summary: {e}")
        return {"error": str(e)}
