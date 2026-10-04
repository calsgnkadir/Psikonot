from abc import ABC, abstractmethod
from typing import Optional, List
from core.domain.entities import User, Block

class IUserRepository(ABC):
    @abstractmethod
    def save_user(self, user: User) -> None:
        """Saves a user entity to storage."""
        pass

    @abstractmethod
    def load_user(self, username: str) -> Optional[User]:
        """Loads a user entity by username."""
        pass

    @abstractmethod
    def load_all_users(self) -> List[User]:
        """Loads all users from storage."""
        pass

    @abstractmethod
    def user_exists(self, username: str) -> bool:
        """Checks if a user exists by username."""
        pass

    @abstractmethod
    def delete_user(self, username: str) -> bool:
        """Deletes a user by username."""
        pass


class IBlockRepository(ABC):
    @abstractmethod
    def save_block(self, project_name: str, block: Block) -> None:
        """Saves a single block to database."""
        pass

    @abstractmethod
    def load_all_blocks(self, project_name: str) -> List[Block]:
        """Loads all blocks for a given project sequentially."""
        pass

    @abstractmethod
    def get_last_index(self, project_name: str) -> int:
        """Returns the last block index, or -1 if the chain is empty."""
        pass

    @abstractmethod
    def project_exists(self, project_name: str) -> bool:
        """Checks if the project blockchain exists."""
        pass

    @abstractmethod
    def create_project(self, project_name: str) -> bool:
        """Creates new project storage if not exists."""
        pass

    @abstractmethod
    def list_projects(self) -> List[str]:
        """Lists all project identifiers (patient_ids)."""
        pass

    @abstractmethod
    def reset_db(self, project_name: str) -> None:
        """Resets/wipes the blockchain database."""
        pass

    @abstractmethod
    def save_block_salt(self, project_name: str, block_index: int, salt: bytes) -> None:
        """Saves a block-specific encryption salt."""
        pass

    @abstractmethod
    def load_block_salt(self, project_name: str, block_index: int) -> Optional[bytes]:
        """Loads a block-specific encryption salt."""
        pass

    @abstractmethod
    def get_patient_salt(self, project_name: str) -> bytes:
        """Gets or generates a patient-specific encryption salt."""
        pass

    @abstractmethod
    def save_patient_salt(self, project_name: str, salt: bytes) -> None:
        """Saves a patient-specific encryption salt."""
        pass

    @abstractmethod
    def save_block_pwd_hash(self, project_name: str, block_index: int, pwd_hash: str) -> None:
        """Saves a block's password hash."""
        pass

    @abstractmethod
    def load_block_pwd_hash(self, project_name: str, block_index: int) -> Optional[str]:
        """Loads a block's password hash."""
        pass

    @abstractmethod
    def save_notarization_tx(self, project_name: str, tx_hash: str) -> None:
        """Saves a patient's latest notarization transaction hash."""
        pass

    @abstractmethod
    def load_notarization_tx(self, project_name: str) -> Optional[str]:
        """Loads a patient's latest notarization transaction hash."""
        pass

    @abstractmethod
    def save_simulated_merkle_root(self, project_name: str, root: str) -> None:
        """Saves a patient's latest simulated Merkle root."""
        pass

    @abstractmethod
    def load_simulated_merkle_root(self, project_name: str) -> Optional[str]:
        """Loads a patient's latest simulated Merkle root."""
        pass

    @abstractmethod
    def get_by_date_range(self, project_name: str, start_date: str, end_date: str) -> List[Block]:
        """Gets blocks within a date range (inclusive)."""
        pass

    @abstractmethod
    def get_by_type(self, project_name: str, record_type: str) -> List[Block]:
        """Gets blocks by record type."""
        pass


class IAuditRepository(ABC):
    @abstractmethod
    def append_audit_log(
        self,
        project_name: str,
        action: str,
        username: str,
        block_index: Optional[int] = None,
        device_id: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> None:
        """Appends a system audit log entry."""
        pass

    @abstractmethod
    def load_audit_logs(self, project_name: str, limit: int = 100) -> List[dict]:
        """Loads system audit logs for a project."""
        pass

    @abstractmethod
    def append_access_log(
        self,
        project_name: str,
        username: str,
        action: str,
        device_id: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> None:
        """Appends a patient-specific access log entry."""
        pass

    @abstractmethod
    def load_access_logs(self, project_name: str, limit: int = 100, offset: int = 0) -> List[dict]:
        """Loads patient-specific access logs."""
        pass

    @abstractmethod
    def verify_access_log_integrity(self, project_name: str) -> dict:
        """Verifies the tamper-evident access ledger; returns valid/count/broken_at."""
        pass
