"""
模块名称：migration

本模块提供数据库迁移相关的实用函数，主要用于检查数据库结构的存在性。
主要功能包括：
- 检查表是否存在
- 检查列是否存在
- 检查外键是否存在
- 检查约束是否存在

设计背景：在数据库迁移过程中，需要检查各种数据库对象是否存在以决定是否需要创建
注意事项：使用SQLAlchemy的inspector来检查数据库对象
"""

import sqlalchemy as sa


def table_exists(name, conn):
    """检查表是否存在。
    
    关键路径（三步）：
    1) 使用SQLAlchemy的inspect函数获取检查器
    2) 获取数据库中的所有表名
    3) 检查目标表名是否在其中
    
    异常流：无显式异常处理
    性能瓶颈：数据库查询性能
    排障入口：检查连接是否有效，表名是否正确
    """
    inspector = sa.inspect(conn)
    return name in inspector.get_table_names()


def column_exists(table_name, column_name, conn):
    """Check if a column exists in a table.

    Parameters:
    table_name (str): The name of the table to check.
    column_name (str): The name of the column to check.
    conn (sqlalchemy.engine.Engine or sqlalchemy.engine.Connection): The SQLAlchemy engine or connection to use.

    Returns:
    bool: True if the column exists, False otherwise.
    """
    inspector = sa.inspect(conn)
    return column_name in [column["name"] for column in inspector.get_columns(table_name)]


def foreign_key_exists(table_name, fk_name, conn):
    """Check if a foreign key exists in a table.

    Parameters:
    table_name (str): The name of the table to check.
    fk_name (str): The name of the foreign key to check.
    conn (sqlalchemy.engine.Engine or sqlalchemy.engine.Connection): The SQLAlchemy engine or connection to use.

    Returns:
    bool: True if the foreign key exists, False otherwise.
    """
    inspector = sa.inspect(conn)
    return fk_name in [fk["name"] for fk in inspector.get_foreign_keys(table_name)]


def constraint_exists(table_name, constraint_name, conn):
    """检查表中是否存在约束。
    
    关键路径（三步）：
    1) 使用SQLAlchemy的inspect函数获取检查器
    2) 获取表中的所有唯一约束信息
    3) 检查目标约束名是否在其中
    
    异常流：无显式异常处理
    性能瓶颈：数据库查询性能
    排障入口：检查表名和约束名是否正确，连接是否有效
    """
    inspector = sa.inspect(conn)
    constraints = inspector.get_unique_constraints(table_name)
    return constraint_name in [constraint["name"] for constraint in constraints]
