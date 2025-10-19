package tema1.t03;

import java.util.Scanner;

public class ej10 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.print("Introduce el primer número: ");
        int num1 = sc.nextInt();

        System.out.print("Introduce el segundo número: ");
        int num2 = sc.nextInt();

        System.out.print("Introduce el tercer número: ");
        int num3 = sc.nextInt();

        System.out.println((num1 <= num2 && num2 <= num3) ? "Los números están ordenados de menor a mayor." : "Los números no están ordenados.");
    }
}
