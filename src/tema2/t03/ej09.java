package tema2.t03;

import java.util.Scanner;

public class ej09 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("Ingresa un numero");
        double a = sc.nextDouble();
        System.out.println("Ingresa otro numero");
        double b = sc.nextDouble();
        
        System.out.println("Operadores:");
        System.out.println("1. SUMA");
        System.out.println("2. RESTA");
        System.out.println("3. MULTIPLICACION");
        System.out.println("4. DIVISION");
        int op = sc.nextInt();
        sc.close();
        
        double resul = Operadores(a, b, op);

        System.out.println("El resultado es: " + resul);
        
    }

    static double Operadores(double a, double b, int op) {
        switch (op) {
            case 1:
                return a + b;
            case 2:
                return a - b;
            case 3:
                return a * b;
            case 4:
                return a / b;
            default:
                return 0;
        }
    }
}
