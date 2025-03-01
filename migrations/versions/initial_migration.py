"""Initial migration

Revision ID: 1a2b3c4d5e6f
Revises: 
Create Date: 2023-03-01 12:00:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e6f'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create the user table
    op.create_table('user',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=100), nullable=False),
        sa.Column('password', sa.String(length=200), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('is_admin', sa.Boolean(), nullable=True),
        sa.Column('is_premium', sa.Boolean(), nullable=True),
        sa.Column('subscription_end_date', sa.DateTime(), nullable=True),
        sa.Column('stripe_customer_id', sa.String(length=100), nullable=True),
        sa.Column('weekly_analyses_count', sa.Integer(), nullable=True),
        sa.Column('last_analysis_reset', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )
    
    # Create the report table
    op.create_table('report',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('company_name', sa.String(length=100), nullable=False),
        sa.Column('job_title', sa.String(length=100), nullable=False),
        sa.Column('location', sa.String(length=100), nullable=True),
        sa.Column('job_details', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('company_analysis', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('salary_analysis', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('final_report', sa.Text(), nullable=True),
        sa.Column('job_posting', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for performance
    op.create_index(op.f('ix_report_created_at'), 'report', ['created_at'], unique=False)
    op.create_index(op.f('ix_report_user_id'), 'report', ['user_id'], unique=False)
    op.create_index(op.f('ix_user_email'), 'user', ['email'], unique=True)


def downgrade():
    op.drop_index(op.f('ix_user_email'), table_name='user')
    op.drop_index(op.f('ix_report_user_id'), table_name='report')
    op.drop_index(op.f('ix_report_created_at'), table_name='report')
    op.drop_table('report')
    op.drop_table('user')
