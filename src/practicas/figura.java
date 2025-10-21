package practicas;

import java.util.Scanner;

public class figura {
    public static void main(String[] args) {
        Scanner sc=new Scanner(System.in);
        int contador = 0;
        System.out.println("Introduce la altura del triangulo:");
        int altura = sc.nextInt();

        for (int i = 1; i <= altura; i++){
            for (int j = 1; j <= i; j++) {
                contador++;
                System.out.print("*");
            }
            System.out.println();
        }
        for (int i = altura - 1; i >= 1; i--){
            for (int j = 1; j <= i; j++) {
                System.out.print("*");
            }
            System.out.println();
        }
    }
}
